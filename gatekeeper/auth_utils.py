import logging
import requests
from django.conf import settings
from django.core.cache import cache
from .mock_discord import get_mock_member_status

logger = logging.getLogger(__name__)

DISCORD_API_BASE = getattr(settings, 'DISCORD_API_BASE', 'https://discord.com/api/v10')


def check_live_guild_membership(user_id):
    """
    Checks if a user is still in the server and possesses 
    either the Guild Member or Alliance role.
    """
    if not user_id:
        return False

    # 1. Mock Mode Intercept
    if getattr(settings, 'MOCK_DISCORD_AUTH', False):
        mock_res = get_mock_member_status()
        if mock_res.get('status_code') == 200:
            roles = set(str(r) for r in mock_res.get('data', {}).get('roles', []))
            return bool(roles.intersection(settings.ALLOWED_DISCORD_ROLES))
        return False

    # 2. Live Discord Bot Check - Cache Lookup
    cache_key = f"discord_membership_{user_id}"
    cached_status = cache.get(cache_key)

    if cached_status is not None:
        return cached_status

    bot_token = getattr(settings, 'DISCORD_BOT_TOKEN', None)
    guild_id = getattr(settings, 'DISCORD_TARGET_GUILD_ID', None)
    allowed_roles = getattr(settings, 'ALLOWED_DISCORD_ROLES', set())

    if not bot_token or not guild_id:
        logger.error("DISCORD_BOT_TOKEN or DISCORD_TARGET_GUILD_ID is not configured.")
        return False

    bot_headers = {'Authorization': f'Bot {bot_token}'}
    member_url = f"{DISCORD_API_BASE}/guilds/{guild_id}/members/{user_id}"

    try:
        response = requests.get(member_url, headers=bot_headers, timeout=5)
    except requests.RequestException as e:
        logger.warning("Discord API request failed for user %s: %s", user_id, e)
        return False

    if response.status_code == 200:
        member_data = response.json()
        user_roles = set(str(r) for r in member_data.get('roles', []))
        is_valid = bool(user_roles.intersection(allowed_roles))

        # Cache valid role for configured TTL (default 60s), negative for 15s
        ttl = getattr(settings, 'DISCORD_MEMBERSHIP_CACHE_TTL', 60)
        cache.set(cache_key, is_valid, timeout=ttl if is_valid else 15)
        return is_valid

    elif response.status_code == 404:
        # User not found in guild (kicked, banned, or not joined)
        logger.info("User %s is not in guild %s (404)", user_id, guild_id)
        cache.set(cache_key, False, timeout=30)
        return False

    elif response.status_code == 429:
        logger.warning("Discord API rate limited when checking user %s", user_id)
        return False

    logger.warning("Unexpected Discord status %s for user %s: %s", response.status_code, user_id, response.text)
    return False
