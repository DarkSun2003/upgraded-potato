import json
import logging
import os
import re
from pathlib import Path
import gspread
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

CACHE_KEY_SHEET = "google_sheet_records"

def rgb_to_css(color_dict):
    """Converts a Google Sheets API color dict to a css rgb(...) string."""
    if not color_dict:
        return ''
    r = int(round(color_dict.get('red', 1) * 255))
    g = int(round(color_dict.get('green', 1) * 255))
    b = int(round(color_dict.get('blue', 1) * 255))
    if (r, g, b) == (255, 255, 255):
        return ''
    return f"rgb({r},{g},{b})"


def resolve_cell_contrast(bg_dict, fg_dict):
    """
    Computes WCAG-compliant high-contrast background and foreground colors.
    Fixes Google Sheets API cases where:
    1. Dark colored cells (Tank blue, Healer green, DPS red, dark gray) have empty/default fg (rendered as black text).
    2. White cells have white fg (e.g. Q/W/P spell slots), rendering text invisible.
    """
    bg_r = bg_dict.get('red', 1.0) if bg_dict else 1.0
    bg_g = bg_dict.get('green', 1.0) if bg_dict else 1.0
    bg_b = bg_dict.get('blue', 1.0) if bg_dict else 1.0
    bg_lum = 0.299 * bg_r + 0.587 * bg_g + 0.114 * bg_b
    is_bg_dark = (bg_lum < 0.65) and (bg_dict is not None)

    has_fg = bool(fg_dict)
    if has_fg:
        fg_r = fg_dict.get('red', 0.0)
        fg_g = fg_dict.get('green', 0.0)
        fg_b = fg_dict.get('blue', 0.0)
        fg_lum = 0.299 * fg_r + 0.587 * fg_g + 0.114 * fg_b
    else:
        fg_r, fg_g, fg_b = 0.0, 0.0, 0.0
        fg_lum = 0.0

    if is_bg_dark:
        # On dark / saturated backgrounds, force bright white text for maximum readability
        if not has_fg or fg_lum < 0.60:
            final_fg = 'rgb(255,255,255)'
        else:
            final_fg = f"rgb({int(round(fg_r * 255))},{int(round(fg_g * 255))},{int(round(fg_b * 255))})"
    else:
        # On light or white backgrounds, ensure text is never invisible white
        if has_fg and fg_lum > 0.68:
            final_fg = 'rgb(32,33,36)'
        elif has_fg:
            final_fg = f"rgb({int(round(fg_r * 255))},{int(round(fg_g * 255))},{int(round(fg_b * 255))})"
        else:
            final_fg = ''

    bg_css = f"rgb({int(round(bg_r * 255))},{int(round(bg_g * 255))},{int(round(bg_b * 255))})" if bg_dict and (bg_r, bg_g, bg_b) != (1.0, 1.0, 1.0) else ''
    return bg_css, final_fg


def get_col_letter(col_idx):
    """Converts 1-indexed column number to letter (1->A, 26->Z, 27->AA)."""
    temp = col_idx - 1
    letter = ''
    while temp >= 0:
        letter = chr((temp % 26) + 65) + letter
        temp = (temp // 26) - 1
    return letter


DEFAULT_ALBION_WEAPONS = [
    'Fill', 'Fill tank', 'Clump Tank', '1h hammer', 'Polehammer', 'Greathammer', 'Grovekeeper', 'Truebolt hammer',
    '1h mace', 'heavy mace', 'Infernal staff', 'HOJ', 'BMS', 'Golem', 'SOB', 'Stillgaze', '1h arcane', 'Great arcane',
    'Hoarfrost', 'Icicle', 'Fill heals', 'Great holy', 'Fallen', 'Redemption', 'Hallowfall', 'Exalted', 'Wild staff',
    'Blight', 'Rampant', 'Forgebark', '1H Nature', 'Fill DPS', 'Gala pair', 'Kingmaker', 'Infinity blade', 'Greataxe',
    'Halberd', 'Infernal scythe', 'Bearpaws', 'Realmbreaker', 'Forge hammers', 'Bracers', 'Spiked', 'Ursine maulers',
    'Hellfire hands', 'Bloodletter', 'Demonfang', 'Bridled', 'Daybreaker', 'Astral', 'Carioncaller', 'Trinity spear',
    '1h crossbow', 'Heavy crossbow', 'Weeping', 'Boltcasters', 'Siegebow', 'Shaper', 'Longbow', 'Wailing', 'Rift glaive',
    'Heron spear', 'Lightcaller', 'Great fire', 'Wildfire', 'Brimstone', 'Dawnsong', 'Glacial', 'Perma', 'Cursed skull',
    'Rotcaller', 'Witchwork', 'Fill support', 'Carving', 'Bedrock', 'Incubus', 'Oathkeepers', 'Dreadstorm', 'Spirithunter',
    'Rootbound', 'Enigmatic', 'Occult', 'Locus', 'Evensong', 'Lifecurse', 'Damnation', 'Shadowcaller', 'Fill BM',
    'Beetle', 'Behemoth', 'Ballista', 'Eagle', 'Rhino', 'Mammy', 'Flame basi', 'Venom basi', 'Spider', 'Juggernaut',
    'Bastion', 'Chariot'
]


def get_weapons_list(spreadsheet=None):
    """Retrieves weapons list from 'Weapons List' tab with fallback to default CTA weapons."""
    cached = cache.get('albion_weapons_list')
    if cached:
        return cached
    if spreadsheet:
        try:
            ws = spreadsheet.worksheet('Weapons List')
            vals = ws.get_values('C3:X8')
            weapons = []
            for r in vals:
                for item in r:
                    val = str(item).strip()
                    if val and val not in weapons:
                        weapons.append(val)
            if weapons:
                cache.set('albion_weapons_list', weapons, timeout=3600)
                return weapons
        except Exception as e:
            logger.warning("Could not fetch Weapons List tab: %s", e)
    cache.set('albion_weapons_list', DEFAULT_ALBION_WEAPONS, timeout=3600)
    return DEFAULT_ALBION_WEAPONS


_range_options_cache = {}


def resolve_validation_options(cond, spreadsheet):
    """Resolves data validation condition to a list of allowed options (dropdown)."""
    if not cond:
        return []
    cond_type = cond.get('type')
    if cond_type == 'ONE_OF_LIST':
        return [v.get('userEnteredValue', '') for v in cond.get('values', []) if v.get('userEnteredValue')]
    elif cond_type == 'ONE_OF_RANGE':
        formula = cond.get('values', [{}])[0].get('userEnteredValue', '')
        if not formula:
            return []
        if formula in _range_options_cache:
            return _range_options_cache[formula]
        m = re.match(r"^=?['\"]?([^'\"!]+)['\"]?!(.+)$", formula)
        if not m:
            return []
        sheet_name = m.group(1).strip()
        cell_range = m.group(2).replace('$', '').strip()
        try:
            ws = spreadsheet.worksheet(sheet_name)
            vals = ws.get_values(cell_range)
            options = []
            for r in vals:
                for item in r:
                    val = str(item).strip()
                    if val and val not in options:
                        options.append(val)
            _range_options_cache[formula] = options
            return options
        except Exception as e:
            logger.warning("Failed to resolve range validation %s: %s", formula, e)
            return []
    return []

# Default mock data for local development when MOCK_DISCORD_AUTH is enabled
_DEFAULT_MOCK_DATA = {
    'headers': ['Member', 'Role', 'Weapon / Build', 'Status'],
    'rows': [
        ['DarkSun', 'Guild Member', 'Permafrost Prism', 'Ready'],
        ['IronClad', 'Guild Member', 'Grovekeeper', 'Ready'],
        ['SwiftHeal', 'Alliance', 'Hallowfall', 'Standby'],
        ['NightStalker', 'Alliance', 'Bloodletter', 'Ready'],
    ]
}

# Mutable in-memory state for local testing
_mock_sheet_store = {
    'headers': list(_DEFAULT_MOCK_DATA['headers']),
    'rows': [list(r) for r in _DEFAULT_MOCK_DATA['rows']],
}


def reset_mock_sheet():
    """Resets the in-memory mock sheet data back to defaults (useful for tests)."""
    global _mock_sheet_store
    _mock_sheet_store = {
        'headers': list(_DEFAULT_MOCK_DATA['headers']),
        'rows': [list(r) for r in _DEFAULT_MOCK_DATA['rows']],
    }


def get_gspread_client():
    """
    Initializes and returns a gspread client.
    Supports:
    1. GOOGLE_CREDENTIALS_JSON environment variable (JSON string)
    2. google_credentials.json file in project root
    3. Render Secret File at /etc/secrets/google_credentials.json
    """
    creds_json = getattr(settings, 'GOOGLE_CREDENTIALS_JSON', None) or os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json:
        try:
            creds_dict = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            if isinstance(creds_dict, dict) and 'private_key' in creds_dict and isinstance(creds_dict['private_key'], str):
                creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n')
            return gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            logger.error("Failed to parse GOOGLE_CREDENTIALS_JSON: %s", e)
            raise ImproperlyConfigured(f"Invalid GOOGLE_CREDENTIALS_JSON: {e}")

    possible_paths = [
        getattr(settings, 'GOOGLE_CREDENTIALS_FILE', None),
        Path(settings.BASE_DIR) / "google_credentials.json",
        Path("/etc/secrets/google_credentials.json"),
    ]

    for p in possible_paths:
        if p and Path(p).is_file():
            return gspread.service_account(filename=str(p))

    raise ImproperlyConfigured(
        "Google credentials not found. Provide either 'google_credentials.json' in your project root, "
        "a Render Secret File at /etc/secrets/google_credentials.json, or set the GOOGLE_CREDENTIALS_JSON environment variable."
    )


def get_sheet_data(force_refresh=False, gid=None):
    """
    Retrieves sheet headers, rows, and all tab metadata.
    If gid is specified, loads that worksheet/tab.
    Otherwise loads the first worksheet.
    Caches results per sheet and tab to minimize Google API quota usage.
    """
    # 1. Return sample data when local mocking is enabled
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        mock_grid = []
        headers = list(_mock_sheet_store['headers'])
        for r_idx, r in enumerate(_mock_sheet_store['rows']):
            row_cells = []
            for c_idx, val in enumerate(r):
                is_weapon = (c_idx == 2 and r_idx > 0)
                row_cells.append({
                    'row': r_idx + 1,
                    'col': c_idx + 1,
                    'rowspan': 1,
                    'colspan': 1,
                    'val': val,
                    'bg': 'rgb(240,244,248)' if r_idx == 0 else '',
                    'fg': '',
                    'bold': (r_idx == 0),
                    'align': 'left',
                    'has_options': is_weapon,
                    'dropdown_type': 'weapons' if is_weapon else '',
                })
            mock_grid.append(row_cells)
        return {
            'doc_title': 'Dungeoneers',
            'headers': headers,
            'rows': [list(r) for r in _mock_sheet_store['rows']],
            'grid_rows': mock_grid,
            'max_cols': len(headers),
            'col_letters': [get_col_letter(i + 1) for i in range(len(headers))],
            'dropdown_presets': {'weapons': DEFAULT_ALBION_WEAPONS},
            'tabs': [
                {'id': '0', 'title': 'Sheet1', 'active': True},
                {'id': '1431495942', 'title': '21.10 Def Mire against Bolty', 'active': False},
            ],
            'active_tab_id': '0',
            'active_tab_title': 'Sheet1',
        }

    # 2. Check cache unless force_refresh is requested
    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    cache_key = f"{CACHE_KEY_SHEET}_{sheet_id}_{gid or 'default'}"
    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return cached_data

    # 3. Fetch from Google Sheets
    gc = get_gspread_client()
    try:
        spreadsheet = gc.open_by_key(sheet_id)
        doc_title = spreadsheet.title
        worksheets = spreadsheet.worksheets()

        target_worksheet = None
        if gid:
            try:
                target_gid_int = int(gid)
                for ws in worksheets:
                    if ws.id == target_gid_int:
                        target_worksheet = ws
                        break
            except (ValueError, TypeError):
                pass

        if not target_worksheet:
            target_worksheet = worksheets[0] if worksheets else spreadsheet.sheet1

        active_gid_str = str(target_worksheet.id)

        tabs = [
            {
                'id': str(ws.id),
                'title': ws.title,
                'active': (str(ws.id) == active_gid_str),
            }
            for ws in worksheets
        ]

        weapons_list = get_weapons_list(spreadsheet)
        dropdown_presets = {
            'weapons': weapons_list
        }

        # Fetch styled grid with formatting, cell background/text colors, merges, and dropdowns
        grid_rows = []
        max_cols = 26
        try:
            rc = getattr(target_worksheet, 'row_count', 65)
            cc = getattr(target_worksheet, 'col_count', 26)
            max_r = min(int(rc) if str(rc).isdigit() else 65, 65)
            max_c = min(int(cc) if str(cc).isdigit() else 26, 26)
            range_str = f"'{target_worksheet.title}'!A1:{get_col_letter(max_c)}{max_r}"

            params = {
                'ranges': [range_str],
                'includeGridData': True,
                'fields': 'sheets(properties,merges,data.rowData.values(formattedValue,effectiveFormat(backgroundColor,textFormat,horizontalAlignment),dataValidation))'
            }
            meta = spreadsheet.fetch_sheet_metadata(params)
            sheet_meta = meta['sheets'][0] if meta.get('sheets') else {}
            merges = sheet_meta.get('merges', [])

            merge_map = {}
            skip_set = set()
            for m in merges:
                s_r, e_r = m.get('startRowIndex', 0), m.get('endRowIndex', 0)
                s_c, e_c = m.get('startColumnIndex', 0), m.get('endColumnIndex', 0)
                rowspan = e_r - s_r
                colspan = e_c - s_c
                merge_map[(s_r, s_c)] = (rowspan, colspan)
                for r in range(s_r, e_r):
                    for c in range(s_c, e_c):
                        if (r, c) != (s_r, s_c):
                            skip_set.add((r, c))

            rows_data = sheet_meta.get('data', [])[0].get('rowData', []) if sheet_meta.get('data') else []
            for r_idx, r in enumerate(rows_data):
                row_cells = []
                vals = r.get('values', [])
                for c_idx, cell in enumerate(vals):
                    if (r_idx, c_idx) in skip_set:
                        continue
                    rowspan, colspan = merge_map.get((r_idx, c_idx), (1, 1))
                    fmt = cell.get('effectiveFormat', {})
                    bg_dict = fmt.get('backgroundColor')
                    tf = fmt.get('textFormat', {})
                    fg_dict = tf.get('foregroundColor')
                    bg, fg = resolve_cell_contrast(bg_dict, fg_dict)
                    bold = tf.get('bold', False)
                    align = fmt.get('horizontalAlignment', '').lower()
                    val = cell.get('formattedValue', '')
                    options = resolve_validation_options(cell.get('dataValidation', {}).get('condition'), spreadsheet)

                    is_weapon_dropdown = False
                    if options and any(opt in weapons_list for opt in options[:10]):
                        is_weapon_dropdown = True
                    elif (c_idx + 1) in (4, 5, 6) and (r_idx + 1) >= 7:
                        is_weapon_dropdown = True
                    elif options:
                        is_weapon_dropdown = True

                    dropdown_type = 'weapons' if is_weapon_dropdown else ''
                    has_options = is_weapon_dropdown or bool(options)

                    row_cells.append({
                        'row': r_idx + 1,
                        'col': c_idx + 1,
                        'rowspan': rowspan,
                        'colspan': colspan,
                        'val': val,
                        'bg': bg,
                        'fg': fg,
                        'bold': bold,
                        'align': align,
                        'has_options': has_options,
                        'dropdown_type': dropdown_type,
                    })
                grid_rows.append(row_cells)

        except Exception as e:
            logger.warning("Could not fetch rich grid formatting, falling back to raw values: %s", e)

        # Fallback if rich grid fetch failed or was empty
        if not grid_rows:
            all_values = target_worksheet.get_all_values()
            for r_idx, r in enumerate(all_values[:65]):
                row_cells = []
                for c_idx, val in enumerate(r[:26]):
                    is_weapon = ((c_idx + 1) in (4, 5, 6) and (r_idx + 1) >= 7)
                    row_cells.append({
                        'row': r_idx + 1,
                        'col': c_idx + 1,
                        'rowspan': 1,
                        'colspan': 1,
                        'val': val,
                        'bg': '',
                        'fg': '',
                        'bold': False,
                        'align': 'left',
                        'has_options': is_weapon,
                        'dropdown_type': 'weapons' if is_weapon else '',
                    })
                grid_rows.append(row_cells)

        # Apply Google Sheets Conditional Formatting: Green both names (Signup List Col 3 <-> Party Player Col 11)
        party_names = set()
        for row in grid_rows:
            for cell in row:
                if cell.get('col') == 11:
                    v = cell.get('val', '').strip().lower()
                    if v and v not in ('player', 'weapon', 'offhand', 'role'):
                        party_names.add(v)

        signup_names = set()
        for row in grid_rows:
            for cell in row:
                if cell.get('col') == 3:
                    v = cell.get('val', '').strip().lower()
                    if v and v not in ('name', 'disarray calculator', 'total signed-up', 'disarray level'):
                        signup_names.add(v)

        matched_names = party_names.intersection(signup_names)
        for row in grid_rows:
            for cell in row:
                if cell.get('col') in (3, 11):
                    v = cell.get('val', '').strip().lower()
                    if v in matched_names:
                        cell['is_green_matched'] = True
                        cell['bg'] = 'rgb(183,225,205)'
                        cell['fg'] = 'rgb(13,101,45)'
                        cell['bold'] = True

        headers = [c['val'] for c in grid_rows[0]] if grid_rows else []
        raw_rows = [[c['val'] for c in r] for r in grid_rows[1:]] if len(grid_rows) > 1 else []
        col_letters = [get_col_letter(i + 1) for i in range(max_cols)]

        data = {
            'doc_title': doc_title,
            'headers': headers,
            'rows': raw_rows,
            'grid_rows': grid_rows,
            'max_cols': max_cols,
            'col_letters': col_letters,
            'dropdown_presets': dropdown_presets,
            'tabs': tabs,
            'active_tab_id': active_gid_str,
            'active_tab_title': target_worksheet.title,
        }

    except Exception as e:
        logger.error("Error accessing Google Sheet with ID %s (gid=%s): %s", sheet_id, gid, e)
        raise

    # 4. Cache response
    ttl = getattr(settings, 'SHEET_CACHE_TTL', 300)
    cache.set(cache_key, data, timeout=ttl)
    return data


def update_sheet_cell(row, col, value, gid=None):
    """
    Updates a single cell in the sheet and invalidates the cache.
    row: 1-based integer (row 1 is header, row 2 is the first data row)
    col: 1-based integer (col 1 is column A)
    value: string content
    gid: optional worksheet ID
    """
    row = int(row)
    col = int(col)
    if row < 1 or col < 1:
        raise ValueError("Row and column indices must be positive integers (1-indexed).")

    # In mock mode, update local in-memory store
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        if row == 1:
            while len(_mock_sheet_store['headers']) < col:
                _mock_sheet_store['headers'].append('')
            _mock_sheet_store['headers'][col - 1] = str(value)
        else:
            data_row_idx = row - 2
            while len(_mock_sheet_store['rows']) <= data_row_idx:
                _mock_sheet_store['rows'].append([''] * len(_mock_sheet_store['headers']))
            while len(_mock_sheet_store['rows'][data_row_idx]) < col:
                _mock_sheet_store['rows'][data_row_idx].append('')
            _mock_sheet_store['rows'][data_row_idx][col - 1] = str(value)

        cache.clear()
        return True

    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    gc = get_gspread_client()
    spreadsheet = gc.open_by_key(sheet_id)

    if gid:
        try:
            worksheet = spreadsheet.get_worksheet_by_id(int(gid))
        except Exception:
            worksheet = spreadsheet.sheet1
    else:
        worksheet = spreadsheet.sheet1

    worksheet.update_cell(row, col, str(value))

    # Invalidate cache for this worksheet tab and default
    cache.delete(f"{CACHE_KEY_SHEET}_{sheet_id}_{gid or 'default'}")
    cache.delete(f"{CACHE_KEY_SHEET}_{sheet_id}_default")
    return True


def add_sheet_row(row_values, gid=None):
    """
    Appends a new row to the sheet and invalidates the cache.
    row_values: list of strings
    gid: optional worksheet ID
    """
    if not isinstance(row_values, (list, tuple)):
        raise ValueError("row_values must be a list or tuple.")

    string_values = [str(v) for v in row_values]

    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        _mock_sheet_store['rows'].append(string_values)
        cache.clear()
        return True

    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    gc = get_gspread_client()
    spreadsheet = gc.open_by_key(sheet_id)

    if gid:
        try:
            worksheet = spreadsheet.get_worksheet_by_id(int(gid))
        except Exception:
            worksheet = spreadsheet.sheet1
    else:
        worksheet = spreadsheet.sheet1

    worksheet.append_row(string_values)

    cache.delete(f"{CACHE_KEY_SHEET}_{sheet_id}_{gid or 'default'}")
    cache.delete(f"{CACHE_KEY_SHEET}_{sheet_id}_default")
    return True


def clear_sheet_cache():
    """Manually invalidates the cached sheet records."""
    cache.clear()
