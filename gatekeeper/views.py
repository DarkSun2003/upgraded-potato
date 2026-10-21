import json
import logging
import secrets
import urllib.parse
import requests

from django.conf import settings
from django.shortcuts import redirect, render
from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.views.decorators.http import require_POST

from .decorators import live_discord_role_required
from .services import get_sheet_data, update_sheet_cell, add_sheet_row
from .auth_utils import check_live_guild_membership
from .mock_discord import get_mock_user_profile

logger = logging.getLogger(__name__)
DISCORD_API_BASE = getattr(settings, 'DISCORD_API_BASE', 'https://discord.com/api/v10')


def discord_login(request):
    """Generates state and redirects to Discord login screen (or mocks it locally)."""
    # If mocking is enabled, bypass redirecting to Discord entirely
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        mock_user = get_mock_user_profile()

        if not check_live_guild_membership(mock_user['id']):
            messages.error(request, "Mock check failed: Insufficient roles or not in guild.")
            return redirect('access_denied')

        request.session['discord_user_id'] = mock_user['id']
        request.session['discord_username'] = mock_user['username']
        return redirect('sheet_dashboard')

    state = secrets.token_urlsafe(16)
    request.session['oauth2_state'] = state

    params = {
        'client_id': settings.DISCORD_CLIENT_ID,
        'response_type': 'code',
        'scope': 'identify',
        'redirect_uri': settings.DISCORD_REDIRECT_URI,
        'state': state,
        'prompt': 'consent'
    }
    url = f"https://discord.com/oauth2/authorize?{urllib.parse.urlencode(params)}"
    return redirect(url)


def discord_callback(request):
    """Exchanges OAuth code for access token and verifies identity."""
    # Check if the user cancelled or Discord returned an error
    error = request.GET.get('error')
    if error:
        error_desc = request.GET.get('error_description', 'Authorization was denied by user.')
        logger.info("Discord OAuth denied: %s (%s)", error, error_desc)
        messages.warning(request, f"Discord login was cancelled: {error_desc}")
        return redirect('access_denied')

    code = request.GET.get('code')
    state = request.GET.get('state')
    saved_state = request.session.pop('oauth2_state', None)

    if not code or not state or state != saved_state:
        messages.error(request, "Invalid or expired authorization request. Please try again.")
        return redirect('discord_login')

    token_url = f"{DISCORD_API_BASE}/oauth2/token"
    token_data = {
        'client_id': settings.DISCORD_CLIENT_ID,
        'client_secret': settings.DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.DISCORD_REDIRECT_URI,
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_res = requests.post(token_url, data=token_data, headers=headers, timeout=10)
    except requests.RequestException as e:
        logger.error("Network error exchanging Discord code: %s", e)
        messages.error(request, "Unable to communicate with Discord authentication service. Please try again.")
        return redirect('access_denied')

    if token_res.status_code != 200:
        logger.error("Discord token exchange failed (%s): %s", token_res.status_code, token_res.text)
        messages.error(request, "Failed to retrieve Discord authorization token.")
        return redirect('access_denied')

    access_token = token_res.json().get('access_token')

    try:
        user_res = requests.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=10
        )
    except requests.RequestException as e:
        logger.error("Network error fetching Discord profile: %s", e)
        messages.error(request, "Could not fetch Discord user profile. Please try again.")
        return redirect('access_denied')

    if user_res.status_code != 200:
        logger.error("Discord user fetch failed (%s): %s", user_res.status_code, user_res.text)
        messages.error(request, "Could not fetch Discord user profile.")
        return redirect('access_denied')

    discord_user = user_res.json()
    user_id = str(discord_user['id'])

    if not check_live_guild_membership(user_id):
        messages.error(request, "Access denied: You must be in our Discord server and possess the required role.")
        return redirect('access_denied')

    # Mitigate session fixation by cycling session key
    request.session.cycle_key()

    request.session['discord_user_id'] = user_id
    request.session['discord_username'] = discord_user.get('username', 'Member')
    avatar_id = discord_user.get('avatar')
    if avatar_id:
        request.session['discord_avatar_url'] = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_id}.png?size=64"
    else:
        request.session['discord_avatar_url'] = None

    return redirect('sheet_dashboard')


@live_discord_role_required
def sheet_dashboard_view(request):
    """Renders the Google Sheet data for verified members."""
    force_refresh = request.GET.get('refresh') == '1'
    gid = request.GET.get('gid')

    try:
        data = get_sheet_data(force_refresh=force_refresh, gid=gid)
        if force_refresh:
            messages.success(request, "Sheet data refreshed from Google Sheets.")
    except Exception as e:
        logger.exception("Failed to retrieve Google Sheet records: %s", e)
        data = {
            'doc_title': 'Spreadsheet',
            'headers': [],
            'grid_rows': [],
            'tabs': [],
            'active_tab_id': '',
            'active_tab_title': '',
            'max_cols': 26,
            'col_letters': [chr(c) for c in range(65, 91)],
        }
        messages.error(request, "Unable to fetch the latest spreadsheet data. Please try again shortly.")

    return render(request, 'dashboard/sheet.html', {
        'doc_title': data.get('doc_title', 'Spreadsheet'),
        'headers': data.get('headers', []),
        'grid_rows': data.get('grid_rows', []),
        'max_cols': data.get('max_cols', 26),
        'col_letters': data.get('col_letters', []),
        'tabs': data.get('tabs', []),
        'active_tab_id': data.get('active_tab_id', ''),
        'active_tab_title': data.get('active_tab_title', ''),
        'username': request.session.get('discord_username', 'Member'),
        'avatar_url': request.session.get('discord_avatar_url'),
    })


@live_discord_role_required
@require_POST
def update_cell_api(request):
    """
    API endpoint for updating a cell in the Google Sheet.
    Protected by Discord role verification.
    Accepts JSON payload: { "row": 2, "col": 1, "value": "New Content", "gid": "1431495942" }
    """
    try:
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = request.POST

        row = int(payload.get('row'))
        col = int(payload.get('col'))
        value = str(payload.get('value', ''))
        gid = payload.get('gid')

        if row < 1 or col < 1:
            return JsonResponse({'success': False, 'error': 'Row and Col must be positive integers (1-indexed).'}, status=400)

        update_sheet_cell(row, col, value, gid=gid)
        return JsonResponse({'success': True, 'message': 'Cell updated successfully.'})

    except (TypeError, ValueError) as e:
        return JsonResponse({'success': False, 'error': f'Invalid input: {e}'}, status=400)
    except Exception as e:
        logger.exception("Error updating sheet cell: %s", e)
        return JsonResponse({'success': False, 'error': 'Failed to update sheet cell.'}, status=500)


@live_discord_role_required
@require_POST
def add_row_api(request):
    """
    API endpoint for appending a new row to the Google Sheet.
    Protected by Discord role verification.
    Accepts JSON payload: { "values": ["val1", "val2", ...], "gid": "1431495942" }
    """
    try:
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = request.POST

        values = payload.get('values')
        gid = payload.get('gid')

        if not isinstance(values, list):
            return JsonResponse({'success': False, 'error': 'values must be a list of strings.'}, status=400)

        add_sheet_row(values, gid=gid)
        return JsonResponse({'success': True, 'message': 'Row added successfully.'})

    except Exception as e:
        logger.exception("Error appending sheet row: %s", e)
        return JsonResponse({'success': False, 'error': 'Failed to add row to sheet.'}, status=500)


def access_denied(request):
    """Fallback view for unauthorized users."""
    invite_url = getattr(settings, 'DISCORD_INVITE_URL', 'https://discord.gg/your-invite-code')
    return render(request, 'access_denied.html', {'discord_invite_url': invite_url})


def discord_logout(request):
    """Flushes active session."""
    request.session.flush()
    return redirect('discord_login')
