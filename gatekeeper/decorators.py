# gatekeeper/decorators.py
from functools import wraps
from django.shortcuts import redirect, render
from django.contrib import messages
from django.http import JsonResponse
from .auth_utils import check_live_guild_membership_detailed, get_guild_preview_data

SOCIAL_BOT_USER_AGENTS = (
    'discordbot',
    'twitterbot',
    'facebookexternalhit',
    'whatsapp',
    'telegrambot',
    'slackbot',
    'linkedinbot',
    'applebot',
    'bingbot',
    'googlebot',
)


def is_social_crawler(request):
    ua = request.META.get('HTTP_USER_AGENT', '').lower()
    return any(bot in ua for bot in SOCIAL_BOT_USER_AGENTS)


def live_discord_role_required(view_func):
    """
    Decorator that verifies both session presence and live Discord guild role membership.
    For API endpoints (/api/), returns appropriate JSON 401/403 status codes.
    For web page views, redirects to login or access denied pages.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        user_id = request.session.get('discord_user_id')

        is_api = request.path.startswith('/api/') or request.headers.get('Accept') == 'application/json'

        if not user_id:
            if is_api:
                return JsonResponse(
                    {'success': False, 'error': 'Authentication required. Please log in via Discord.'},
                    status=401
                )
            if is_social_crawler(request):
                preview_data = get_guild_preview_data()
                return render(request, 'preview.html', {'preview': preview_data})
            return redirect('discord_login')

        is_valid, reason = check_live_guild_membership_detailed(user_id, is_active_session=True)
        if not is_valid:
            request.session.flush()
            if is_api:
                return JsonResponse(
                    {'success': False, 'error': reason or 'Access denied: You do not hold the required role in the Discord server.'},
                    status=403
                )
            messages.error(request, reason or "Access denied: You must hold the required role in the Discord server.")
            return redirect('access_denied')

        return view_func(request, *args, **kwargs)
    return _wrapped_view
