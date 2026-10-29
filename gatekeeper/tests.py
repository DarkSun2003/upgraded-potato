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
    get_weapons_list,
    DEFAULT_ALBION_WEAPONS,
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

    def test_access_denied_page_renders_without_invite_url(self):
        """Access denied page renders cleanly without any invite URLs or buttons."""
        response = self.client.get(reverse('access_denied'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sanctum Sealed')
        self.assertContains(response, 'Switch Account / Re-Authenticate')
        self.assertNotContains(response, 'Enter The Discord Citadel')
        self.assertNotContains(response, 'discord.gg')

    @patch('gatekeeper.auth_utils.requests.get')
    def test_detailed_membership_reports_401_bot_token_error(self, mock_discord_get):
        """401 from Discord surfaces a clear bot token configuration error."""
        from gatekeeper.auth_utils import check_live_guild_membership_detailed
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_discord_get.return_value = mock_response

        is_valid, reason = check_live_guild_membership_detailed('999999')
        self.assertFalse(is_valid)
        self.assertIn('bot token is invalid', reason.lower())

    @patch('gatekeeper.decorators.get_guild_preview_data')
    def test_social_crawler_gets_opengraph_preview(self, mock_preview):
        """Discordbot and social crawlers get 200 OK with OpenGraph embed tags."""
        mock_preview.return_value = {
            'title': 'The Dungeoneers | Guild Tools',
            'description': 'Guild management CTA signups for The Dungeoneers and alies in Albion Online.',
            'image': 'https://cdn.discordapp.com/icons/1437495042435453010/058302274c65d4abc1af045b00e9447c.png?size=512',
            'url': 'https://dareirus-sheet.onrender.com/',
            'theme_color': '#28502A',
        }
        self.client.session.flush()
        response = self.client.get('/', HTTP_USER_AGENT='Mozilla/5.0 (compatible; Discordbot/2.0; +https://discord.app)')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The Dungeoneers | Guild Tools')
        self.assertContains(response, 'og:image')
        self.assertContains(response, 'cdn.discordapp.com')
        self.assertContains(response, '#28502A')


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
        mock_update_cell.assert_called_once_with(2, 1, 'NewLeader', gid=None)

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
        mock_add_row.assert_called_once_with(['Newbie', 'Guild Member', 'Bow', 'Ready'], gid=None)


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
        mock_worksheet.id = 0
        mock_worksheet.title = 'Sheet1'
        mock_worksheet.get_all_values.return_value = [
            ['Name', 'Class'],
            ['Player1', 'Warrior']
        ]
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.title = 'Sample Sheet'
        mock_spreadsheet.worksheets.return_value = [mock_worksheet]
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_gc.open_by_key.return_value = mock_spreadsheet
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
        self.assertIsNone(cache.get('google_sheet_records_sample_sheet_default'))
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


class DropdownAndFormattingTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_default_albion_weapons_list(self):
        """Verify default Albion weapons list contains required weapons from Weapons List."""
        weapons = get_weapons_list(None)
        self.assertTrue(len(weapons) >= 100)
        self.assertIn("Hallowfall", weapons)
        self.assertIn("Perma", weapons)
        self.assertIn("Golem", weapons)
        self.assertIn("Spiked", weapons)
        self.assertIn("Bedrock", weapons)

    @patch('gatekeeper.services.get_gspread_client')
    def test_green_conditional_formatting_matched_names(self, mock_get_client):
        """When signup name (col 3) matches party player (col 11), both get highlighted green."""
        mock_gc = MagicMock()
        mock_worksheet = MagicMock()
        mock_worksheet.id = '123'
        mock_worksheet.title = 'CTA Sheet'
        mock_worksheet.row_count = 10
        mock_worksheet.col_count = 15

        # Row with signup name in col 3 (index 2) and party player in col 11 (index 10)
        grid_data = [
            ['Header1', 'Header2', 'DarkSun', '', '', '', '', '', '', '', 'DarkSun', '']
        ]
        mock_worksheet.get_all_values.return_value = grid_data

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.title = 'CTA'
        mock_spreadsheet.worksheets.return_value = [mock_worksheet]
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.side_effect = Exception("No rich metadata")
        mock_gc.open_by_key.return_value = mock_spreadsheet
        mock_get_client.return_value = mock_gc

        with override_settings(GOOGLE_SHEET_ID='test_sheet_id', MOCK_DISCORD_AUTH=False):
            data = get_sheet_data(force_refresh=True)

        cells = data['grid_rows'][0]
        col3_cell = next(c for c in cells if c['col'] == 3)
        col11_cell = next(c for c in cells if c['col'] == 11)

        self.assertTrue(col3_cell.get('is_green_matched'))
        self.assertTrue(col11_cell.get('is_green_matched'))
        self.assertEqual(col3_cell.get('bg'), 'rgb(183,225,205)')
        self.assertEqual(col11_cell.get('bg'), 'rgb(183,225,205)')

    @patch('gatekeeper.services.get_gspread_client')
    def test_dropdowns_only_on_weapon_columns_b_to_d_not_e_and_f(self, mock_get_client):
        """Dropdowns should only be applied to columns B to D when headers designate B-D, not E and F."""
        mock_gc = MagicMock()
        mock_worksheet = MagicMock()
        mock_worksheet.id = '123'
        mock_worksheet.title = 'CTA Sheet'
        mock_worksheet.row_count = 10
        mock_worksheet.col_count = 10

        grid_data = [
            ['Name', 'Priority 1', 'Priority 2', 'Priority 3', 'Notes', 'Role'], # Row 1 (Header)
            ['Player1', 'Perma', 'Golem', 'Spiked', 'Some note', 'DPS'],       # Row 2 (Data)
        ]
        mock_worksheet.get_all_values.return_value = grid_data

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.title = 'CTA'
        mock_spreadsheet.worksheets.return_value = [mock_worksheet]
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.side_effect = Exception("No rich metadata")
        mock_gc.open_by_key.return_value = mock_spreadsheet
        mock_get_client.return_value = mock_gc

        with override_settings(GOOGLE_SHEET_ID='test_sheet_id', MOCK_DISCORD_AUTH=False):
            data = get_sheet_data(force_refresh=True)

        data_row = data['grid_rows'][1]
        col_b = next(c for c in data_row if c['col'] == 2) # Col B
        col_c = next(c for c in data_row if c['col'] == 3) # Col C
        col_d = next(c for c in data_row if c['col'] == 4) # Col D
        col_e = next(c for c in data_row if c['col'] == 5) # Col E
        col_f = next(c for c in data_row if c['col'] == 6) # Col F

        self.assertTrue(col_b.get('has_options'))
        self.assertTrue(col_c.get('has_options'))
        self.assertTrue(col_d.get('has_options'))
        self.assertFalse(col_e.get('has_options'))
        self.assertFalse(col_f.get('has_options'))
    @patch('gatekeeper.services.get_gspread_client')
    def test_green_conditional_formatting_col1_and_col8_real_sheet_layout(self, mock_get_client):
        """When signup name is in Col 1 (A) and party player is in Col 8 (H), both turn green."""
        mock_gc = MagicMock()
        mock_worksheet = MagicMock()
        mock_worksheet.id = '123'
        mock_worksheet.title = 'CTA Sheet'
        mock_worksheet.row_count = 10
        mock_worksheet.col_count = 15

        grid_data = [
            ['Name', 'Priority 1', 'Priority 2', 'Priority 3', '', 'Role', 'Weapon', 'Player'],
            ['Dare', 'Golem', 'Perma', 'Spiked', '', 'Tank', 'Pole', 'Dare'],
        ]
        mock_worksheet.get_all_values.return_value = grid_data

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.title = 'CTA'
        mock_spreadsheet.worksheets.return_value = [mock_worksheet]
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.side_effect = Exception("No rich metadata")
        mock_gc.open_by_key.return_value = mock_spreadsheet
        mock_get_client.return_value = mock_gc

        with override_settings(GOOGLE_SHEET_ID='test_sheet_id', MOCK_DISCORD_AUTH=False):
            data = get_sheet_data(force_refresh=True)

        data_row = data['grid_rows'][1]
        col1_cell = next(c for c in data_row if c['col'] == 1) # Dare signup
        col8_cell = next(c for c in data_row if c['col'] == 8) # Dare party player

        self.assertTrue(col1_cell.get('is_green_matched'))
        self.assertTrue(col8_cell.get('is_green_matched'))
        self.assertEqual(col1_cell.get('bg'), 'rgb(183,225,205)')
        self.assertEqual(col8_cell.get('bg'), 'rgb(183,225,205)')
    @patch('gatekeeper.services.get_gspread_client')
    def test_unmatched_player_slot_reverts_to_grey(self, mock_get_client):
        """Unmatched player slot or empty slot in party roster should revert to grey rgb(183,183,183)."""
        mock_gc = MagicMock()
        mock_worksheet = MagicMock()
        mock_worksheet.id = '123'
        mock_worksheet.title = 'CTA Sheet'
        mock_worksheet.row_count = 10
        mock_worksheet.col_count = 15

        grid_data = [
            ['Name', 'Priority 1', 'Priority 2', 'Priority 3', '', 'Role', 'Weapon', 'Player'],
            ['Dare', 'Golem', 'Perma', 'Spiked', '', 'Tank', 'Pole', ''], # Empty party slot
        ]
        mock_worksheet.get_all_values.return_value = grid_data

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.title = 'CTA'
        mock_spreadsheet.worksheets.return_value = [mock_worksheet]
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.side_effect = Exception("No rich metadata")
        mock_gc.open_by_key.return_value = mock_spreadsheet
        mock_get_client.return_value = mock_gc

        with override_settings(GOOGLE_SHEET_ID='test_sheet_id', MOCK_DISCORD_AUTH=False):
            data = get_sheet_data(force_refresh=True)

        data_row = data['grid_rows'][1]
        col1_cell = next(c for c in data_row if c['col'] == 1) # Dare signup (unmatched)
        col8_cell = next(c for c in data_row if c['col'] == 8) # Empty party slot

        self.assertFalse(col1_cell.get('is_green_matched', False))
        self.assertTrue(col1_cell.get('is_unmatched_player_slot'))
        self.assertEqual(col1_cell.get('bg'), 'rgb(183,183,183)')

        self.assertFalse(col8_cell.get('is_green_matched', False))
        self.assertTrue(col8_cell.get('is_unmatched_player_slot'))
        self.assertEqual(col8_cell.get('bg'), 'rgb(183,183,183)')

    @override_settings(MOCK_DISCORD_AUTH=True)
    def test_mock_mode_defaults_to_last_tab(self):
        """In mock mode, visiting without gid should default to the last tab."""
        data = get_sheet_data()
        self.assertEqual(data['active_tab_id'], '1431495942')
        self.assertEqual(data['active_tab_title'], '21.10 Def Mire against Bolty')

        # When gid is explicitly passed, it should load that tab
        data_first = get_sheet_data(gid='0')
        self.assertEqual(data_first['active_tab_id'], '0')
        self.assertEqual(data_first['active_tab_title'], 'Sheet1')

    @override_settings(MOCK_DISCORD_AUTH=False, GOOGLE_SHEET_ID='multi_tab_sheet')
    @patch('gatekeeper.services.get_gspread_client')
    def test_real_sheet_defaults_to_last_tab_skipping_reference(self, mock_get_client):
        """In real sheets, visiting without gid defaults to the last CTA tab, ignoring Weapons List."""
        from gatekeeper.services import resolve_target_worksheet

        mock_gc = MagicMock()
        ws_weapons = MagicMock()
        ws_weapons.id = 1553463428
        ws_weapons.title = 'Weapons List'

        ws_cta1 = MagicMock()
        ws_cta1.id = 101
        ws_cta1.title = 'Old CTA'

        ws_cta2 = MagicMock()
        ws_cta2.id = 202
        ws_cta2.title = 'Latest CTA 21:00'

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheets.return_value = [ws_weapons, ws_cta1, ws_cta2]
        mock_spreadsheet.sheet1 = ws_weapons
        mock_gc.open_by_key.return_value = mock_spreadsheet
        mock_get_client.return_value = mock_gc

        # Default resolution (no gid) should pick ws_cta2 (the last sheet)
        resolved_default = resolve_target_worksheet(mock_spreadsheet, gid=None)
        self.assertEqual(resolved_default.id, 202)
        self.assertEqual(resolved_default.title, 'Latest CTA 21:00')

        # Explicit gid resolution should pick requested sheet
        resolved_explicit = resolve_target_worksheet(mock_spreadsheet, gid='101')
        self.assertEqual(resolved_explicit.id, 101)
        self.assertEqual(resolved_explicit.title, 'Old CTA')

        # If Weapons List is accidentally last, it picks the last non-reference CTA sheet
        mock_spreadsheet.worksheets.return_value = [ws_cta1, ws_cta2, ws_weapons]
        resolved_skip_ref = resolve_target_worksheet(mock_spreadsheet, gid=None)
        self.assertEqual(resolved_skip_ref.id, 202)
        self.assertEqual(resolved_skip_ref.title, 'Latest CTA 21:00')





