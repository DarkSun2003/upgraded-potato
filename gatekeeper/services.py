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
    Supports either:
    1. GOOGLE_CREDENTIALS_JSON environment variable (JSON string, recommended for cloud deployments)
    2. google_credentials.json file on disk (default local file)
    """
    creds_json = getattr(settings, 'GOOGLE_CREDENTIALS_JSON', None) or os.getenv('GOOGLE_CREDENTIALS_JSON')
    if creds_json:
        try:
            creds_dict = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
            return gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            logger.error("Failed to parse GOOGLE_CREDENTIALS_JSON: %s", e)
            raise ImproperlyConfigured(f"Invalid GOOGLE_CREDENTIALS_JSON: {e}")

    creds_file = getattr(settings, 'GOOGLE_CREDENTIALS_FILE', None)
    if not creds_file:
        creds_path = Path(settings.BASE_DIR) / "google_credentials.json"
    else:
        creds_path = Path(creds_file)

    if creds_path.is_file():
        return gspread.service_account(filename=str(creds_path))

    raise ImproperlyConfigured(
        f"Google credentials not found. Provide 'google_credentials.json' at {creds_path} "
        "or set the GOOGLE_CREDENTIALS_JSON environment variable."
    )


def get_sheet_data(force_refresh=False):
    """
    Retrieves sheet headers and rows.
    Caches results to reduce Google Sheets API quotas.
    Set force_refresh=True to bypass cache and fetch fresh records.
    """
    # 1. Return sample data when local mocking is enabled
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        return {
            'headers': list(_mock_sheet_store['headers']),
            'rows': [list(r) for r in _mock_sheet_store['rows']],
        }

    # 2. Check cache unless force_refresh is requested
    if not force_refresh:
        cached_data = cache.get(CACHE_KEY_SHEET)
        if cached_data is not None:
            return cached_data

    # 3. Fetch from Google Sheets
    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    gc = get_gspread_client()
    worksheet = gc.open_by_key(sheet_id).sheet1
    all_values = worksheet.get_all_values()

    if not all_values:
        data = {"headers": [], "rows": []}
    else:
        data = {
            "headers": all_values[0],
            "rows": all_values[1:],
        }

    # 4. Cache response
    ttl = getattr(settings, 'SHEET_CACHE_TTL', 300)
    cache.set(CACHE_KEY_SHEET, data, timeout=ttl)
    return data


def update_sheet_cell(row, col, value):
    """
    Updates a single cell in the sheet and invalidates the cache.
    row: 1-based integer (row 1 is header, row 2 is the first data row)
    col: 1-based integer (col 1 is column A)
    value: string content
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

        cache.delete(CACHE_KEY_SHEET)
        return True

    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    gc = get_gspread_client()
    worksheet = gc.open_by_key(sheet_id).sheet1
    worksheet.update_cell(row, col, str(value))

    # Invalidate cache so subsequent views load updated data
    cache.delete(CACHE_KEY_SHEET)
    return True


def add_sheet_row(row_values):
    """
    Appends a new row to the sheet and invalidates the cache.
    row_values: list of strings
    """
    if not isinstance(row_values, (list, tuple)):
        raise ValueError("row_values must be a list or tuple.")

    string_values = [str(v) for v in row_values]

    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        _mock_sheet_store['rows'].append(string_values)
        cache.delete(CACHE_KEY_SHEET)
        return True

    sheet_id = getattr(settings, 'GOOGLE_SHEET_ID', None)
    if not sheet_id:
        raise ImproperlyConfigured("GOOGLE_SHEET_ID is not configured in settings.")

    gc = get_gspread_client()
    worksheet = gc.open_by_key(sheet_id).sheet1
    worksheet.append_row(string_values)

    cache.delete(CACHE_KEY_SHEET)
    return True


def clear_sheet_cache():
    """Manually invalidates the cached sheet records."""
    cache.delete(CACHE_KEY_SHEET)
