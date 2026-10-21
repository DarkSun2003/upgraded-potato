import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

from gatekeeper.services import (
    get_sheet_data,
    update_sheet_cell,
    add_sheet_row,
    reset_mock_sheet,
    CACHE_KEY_SHEET
)


@override_settings(
    MOCK_DISCORD_AUTH=False,
    ALLOWED_DISCORD_ROLES={'111111', '222222'},
    GUILD_MEMBER_ROLE_ID='111111',
    ALLIANCE_ROLE_ID='222222',
    GOOGLE_SHEET_ID='test_sheet_id'
)
class GatekeeperAccessTests(TestCase):

    def setUp(self):
        cache.clear()
        reset_mock_sheet()
        session = self.client.session
        session['discord_user_id'] = '999999'
        session['discord_username'] = 'DarkSun_Test'
        session.save()

    @patch('gatekeeper.views.get_sheet_data')
    @patch('gatekeeper.auth_utils.requests.get')
    def test_allowed_guild_member_can_view_sheet(self, mock_discord_get, mock_sheet_data):
        """User with Guild Member role (111111) gets 200 OK and sees sheet rows."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'roles': ['111111']}
        mock_discord_get.return_value = mock_response

        mock_sheet_data.return_value = {
            'headers': ['Player', 'Role'],
            'rows': [['DarkSun', 'Caller']]
        }

        response = self.client.get(reverse('sheet_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'DarkSun')

    @patch('gatekeeper.auth_utils.requests.get')
    def test_member_without_allowed_role_is_rejected(self, mock_discord_get):
        """User in server but missing Guild/Alliance role gets redirected to access_denied."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'roles': ['555555']}  # Unapproved role
        mock_discord_get.return_value = mock_response

        response = self.client.get(reverse('sheet_dashboard'))
        self.assertRedirects(response, reverse('access_denied'))
        self.assertNotIn('discord_user_id', self.client.session)

    @patch('gatekeeper.auth_utils.requests.get')
    def test_kicked_member_session_is_flushed(self, mock_discord_get):
        """User who left or was kicked (404) loses session immediately."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_discord_get.return_value = mock_response

        response = self.client.get(reverse('sheet_dashboard'))
        self.assertRedirects(response, reverse('access_denied'))
        self.assertNotIn('discord_user_id', self.client.session)


@override_settings(
    MOCK_DISCORD_AUTH=False,
    ALLOWED_DISCORD_ROLES={'111111'},
    GUILD_MEMBER_ROLE_ID='111111',
    GOOGLE_SHEET_ID='test_sheet_id'
)
class SheetEditingAPITests(TestCase):

    def setUp(self):
        cache.clear()
        reset_mock_sheet()
        session = self.client.session
        session['discord_user_id'] = '999999'
        session['discord_username'] = 'DarkSun_Test'
        session.save()

    @patch('gatekeeper.views.update_sheet_cell')
    @patch('gatekeeper.auth_utils.requests.get')
    def test_update_cell_api_success(self, mock_discord_get, mock_update_cell):
        """Verified member can successfully update a sheet cell."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'roles': ['111111']}
        mock_discord_get.return_value = mock_response
        mock_update_cell.return_value = True

        payload = {'row': 2, 'col': 1, 'value': 'NewLeader'}
        response = self.client.post(
            reverse('api_update_cell'),
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        mock_update_cell.assert_called_once_with(2, 1, 'NewLeader')

    def test_update_cell_unauthenticated_rejected(self):
        """Unauthenticated user receives a 401 JSON error from the API."""
        self.client.session.flush()
        response = self.client.post(
            reverse('api_update_cell'),
            data=json.dumps({'row': 2, 'col': 1, 'value': 'Hacker'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertFalse(data.get('success'))

    @patch('gatekeeper.auth_utils.requests.get')
    def test_update_cell_invalid_payload(self, mock_discord_get):
        """Invalid row/col returns a 400 Bad Request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'roles': ['111111']}
        mock_discord_get.return_value = mock_response

        response = self.client.post(
            reverse('api_update_cell'),
            data=json.dumps({'row': 0, 'col': -1, 'value': 'Bad'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)

    @patch('gatekeeper.views.add_sheet_row')
    @patch('gatekeeper.auth_utils.requests.get')
    def test_add_row_api_success(self, mock_discord_get, mock_add_row):
        """Verified member can append a row."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'roles': ['111111']}
        mock_discord_get.return_value = mock_response
        mock_add_row.return_value = True

        payload = {'values': ['Newbie', 'Guild Member', 'Bow', 'Ready']}
        response = self.client.post(
            reverse('api_add_row'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('success'))
        mock_add_row.assert_called_once_with(['Newbie', 'Guild Member', 'Bow', 'Ready'])


class ServicesUnitTests(TestCase):

    def setUp(self):
        cache.clear()
        reset_mock_sheet()

    @override_settings(MOCK_DISCORD_AUTH=True)
    def test_mock_get_sheet_and_update(self):
        """Services properly fetch and update mock data in local dev mode."""
        data = get_sheet_data()
        self.assertEqual(data['headers'], ['Member', 'Role', 'Weapon / Build', 'Status'])
        self.assertEqual(data['rows'][0][0], 'DarkSun')

        # Update row 2 (which is data row 0: 'DarkSun') col 1 to 'DarkSun_Updated'
        update_sheet_cell(2, 1, 'DarkSun_Updated')

        refreshed = get_sheet_data()
        self.assertEqual(refreshed['rows'][0][0], 'DarkSun_Updated')

    @override_settings(MOCK_DISCORD_AUTH=True)
    def test_mock_add_row(self):
        """Adding a row in mock mode appends to the store."""
        initial_count = len(get_sheet_data()['rows'])
        add_sheet_row(['Tanker', 'Alliance', 'Mace', 'Ready'])
        updated = get_sheet_data()
        self.assertEqual(len(updated['rows']), initial_count + 1)
        self.assertEqual(updated['rows'][-1][0], 'Tanker')

    @override_settings(MOCK_DISCORD_AUTH=False, GOOGLE_SHEET_ID='sample_sheet')
    @patch('gatekeeper.services.get_gspread_client')
    def test_real_sheet_caching_and_invalidation(self, mock_get_client):
        """Verify sheet data is cached and invalidated on cell update."""
        mock_gc = MagicMock()
        mock_worksheet = MagicMock()
        mock_worksheet.get_all_values.return_value = [
            ['Name', 'Class'],
            ['Player1', 'Warrior']
        ]
        mock_gc.open_by_key.return_value.sheet1 = mock_worksheet
        mock_get_client.return_value = mock_gc

        # First read: hits Google Sheets
        first_call = get_sheet_data()
        self.assertEqual(first_call['rows'][0][0], 'Player1')
        self.assertEqual(mock_worksheet.get_all_values.call_count, 1)

        # Second read: hits cache (no new get_all_values call)
        second_call = get_sheet_data()
        self.assertEqual(second_call['rows'][0][0], 'Player1')
        self.assertEqual(mock_worksheet.get_all_values.call_count, 1)

        # Update cell: invalidates cache
        update_sheet_cell(2, 1, 'PlayerOne')
        self.assertIsNone(cache.get(CACHE_KEY_SHEET))
        mock_worksheet.update_cell.assert_called_once_with(2, 1, 'PlayerOne')


class OAuthFlowTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_discord_callback_cancelled_by_user(self):
        """If user clicks cancel on Discord authorization, redirect to access_denied."""
        response = self.client.get(reverse('discord_callback') + '?error=access_denied&error_description=User+declined')
        self.assertRedirects(response, reverse('access_denied'))

    def test_discord_callback_mismatched_state(self):
        """If state does not match session, redirect back to login."""
        session = self.client.session
        session['oauth2_state'] = 'expected_state'
        session.save()

        response = self.client.get(reverse('discord_callback') + '?code=fake_code&state=wrong_state')
        self.assertRedirects(response, reverse('discord_login'), fetch_redirect_response=False)
