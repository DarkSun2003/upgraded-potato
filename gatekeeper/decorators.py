# gatekeeper/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.http import JsonResponse
from .auth_utils import check_live_guild_membership


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
            return redirect('discord_login')

        if not check_live_guild_membership(user_id):
            request.session.flush()
            if is_api:
                return JsonResponse(
                    {'success': False, 'error': 'Access denied: You do not hold the required role in the Discord server.'},
                    status=403
                )
            messages.error(request, "Access denied: You must hold the required role in the Discord server.")
            return redirect('access_denied')

        return view_func(request, *args, **kwargs)
    return _wrapped_view
