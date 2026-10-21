# gatekeeper/mock_discord.py
from django.conf import settings

def get_mock_user_profile():
    return {
        'id': '999999999999999999',
        'username': 'DarkSun_Dev',
        'discriminator': '0',
    }

def get_mock_member_status():
    scenario = getattr(settings, 'MOCK_DISCORD_SCENARIO', 'allowed')

    if scenario == 'allowed':
        # Has the Guild Member role ID defined in your settings
        guild_role = str(getattr(settings, 'GUILD_MEMBER_ROLE_ID', '111111111111111111'))
        return {
            'status_code': 200,
            'data': {'roles': [guild_role]}
        }
    
    elif scenario == 'unauthorized_role':
        # In server, but only has an irrelevant role (e.g., Guest)
        return {
            'status_code': 200,
            'data': {'roles': ['555555555555555555']}
        }

    elif scenario == 'not_in_server':
        # Simulates 404 (user kicked, banned, or never joined)
        return {
            'status_code': 404,
            'data': {'message': 'Unknown Member'}
        }

    return {'status_code': 404, 'data': {}}