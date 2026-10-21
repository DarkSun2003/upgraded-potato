import json
import logging
import os
from pathlib import Path
import gspread
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

CACHE_KEY_SHEET = "google_sheet_records"

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
        return {
            'doc_title': 'Dungeoneers',
            'headers': list(_mock_sheet_store['headers']),
            'rows': [list(r) for r in _mock_sheet_store['rows']],
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

        all_values = target_worksheet.get_all_values()
        active_gid_str = str(target_worksheet.id)

        tabs = [
            {
                'id': str(ws.id),
                'title': ws.title,
                'active': (str(ws.id) == active_gid_str),
            }
            for ws in worksheets
        ]

        data = {
            'doc_title': doc_title,
            'headers': all_values[0] if all_values else [],
            'rows': all_values[1:] if len(all_values) > 1 else [],
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
