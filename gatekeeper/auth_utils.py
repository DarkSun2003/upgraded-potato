import logging
import requests
from django.conf import settings
from django.core.cache import cache
from .mock_discord import get_mock_member_status

logger = logging.getLogger(__name__)

DISCORD_API_BASE = getattr(settings, 'DISCORD_API_BASE', 'https://discord.com/api/v10')


def check_live_guild_membership_detailed(user_id):
    """
    Checks if a user is in the server and possesses an approved guild role.
    Returns: (is_valid: bool, reason: str or None)
    """
    if not user_id:
        return False, "Missing Discord User ID."

    # 1. Mock Mode Intercept
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        mock_res = get_mock_member_status()
        if mock_res.get('status_code') == 200:
            roles = set(str(r) for r in mock_res.get('data', {}).get('roles', []))
            is_valid = bool(roles.intersection(settings.ALLOWED_DISCORD_ROLES))
            return is_valid, None if is_valid else "User does not have an approved guild role."
        return False, "Mock Discord authorization failed."

    # 2. Live Discord Bot Check - Cache Lookup
    cache_key = f"discord_membership_{user_id}"
    cached_status = cache.get(cache_key)

    if cached_status is not None:
        return cached_status, None if cached_status else "Access denied: Account lacks required guild roles or is not in the server."

    bot_token = getattr(settings, 'DISCORD_BOT_TOKEN', None)
    guild_id = getattr(settings, 'DISCORD_TARGET_GUILD_ID', None)
    allowed_roles = getattr(settings, 'ALLOWED_DISCORD_ROLES', set())

    if not bot_token or not guild_id:
        logger.error("DISCORD_BOT_TOKEN or DISCORD_TARGET_GUILD_ID is not configured.")
        return False, "Discord bot authentication or guild ID is not configured on the server."

    bot_headers = {'Authorization': f'Bot {bot_token}'}
    member_url = f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user_id}"

    try:
        response = requests.get(member_url, headers=bot_headers, timeout=5)
    except requests.RequestException as e:
        logger.warning("Discord API request failed for user %s: %s", user_id, e)
        return False, "Network error communicating with Discord. Please try again."

    if response.status_code == 200:
        member_data = response.json()
        user_roles = set(str(r) for r in member_data.get('roles', []))
        is_valid = bool(user_roles.intersection(allowed_roles))

        ttl = getattr(settings, 'DISCORD_MEMBERSHIP_CACHE_TTL', 60)
        cache.set(cache_key, is_valid, timeout=ttl if is_valid else 15)
        if not is_valid:
            return False, "Access denied: Your Discord account is in the server, but does not possess the Guild Member or Alliance role."
        return True, None

    elif response.status_code == 404:
        logger.info("User %s is not in guild %s (404)", user_id, guild_id)
        cache.set(cache_key, False, timeout=30)
        return False, "Access denied: Your Discord account was not found in The Dungeoneers server. Please join the Discord server first."

    elif response.status_code == 401:
        logger.error("Discord bot token is unauthorized (401)")
        return False, "Discord Authentication Error (HTTP 401): The bot token is invalid. Please verify DISCORD_BOT_TOKEN in Render."

    elif response.status_code == 403:
        logger.error("Discord bot missing permissions or Server Members Intent (403)")
        return False, "Discord Permission Error (HTTP 403): The bot lacks permission to view server members. Ensure 'Server Members Intent' is toggled ON in the Discord Developer Portal."

    elif response.status_code == 429:
        logger.warning("Discord API rate limited when checking user %s", user_id)
        return False, "Discord API is currently rate limited. Please try again in a few moments."

    logger.warning("Unexpected Discord status %s for user %s: %s", response.status_code, user_id, response.text)
    return False, f"Discord verification failed (status {response.status_code})."


def check_live_guild_membership(user_id):
    """
    Checks if a user is still in the server and possesses 
    either the Guild Member or Alliance role.
    Backwards-compatible wrapper returning only boolean.
    """
    is_valid, _ = check_live_guild_membership_detailed(user_id)
    return is_valid
