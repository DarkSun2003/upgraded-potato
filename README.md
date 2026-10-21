# Discord Google Sheet Gatekeeper

A secure, Discord role-gated web dashboard that proxies and protects Google Sheets data. Only verified Discord members holding authorized roles (e.g. Guild Member or Alliance) can access, view, and edit the spreadsheet.

---

## 🛡️ Architecture & Security

Traditional methods of sharing a Google Sheet inside a private community either require adding dozens of Google email accounts manually or making the spreadsheet link public, which allows anyone with the link to bypass Discord authentication.

This project solves that problem with a **secure proxy architecture**:
1. **Private Google Sheet**: The Google Sheet remains 100% private in Google Drive, shared exclusively with a dedicated Google Service Account.
2. **Discord OAuth2 & Bot Verification**: Users sign in with Discord (`identify` scope). A bot token verifies that the user is actively in the target Discord server and possesses the required role IDs.
3. **Live Gatekeeper**: Membership is verified live on protected endpoints and cached with short TTLs. If a member is kicked, banned, or loses their role in Discord, their access is immediately revoked on their next action.
4. **Interactive In-Place Editing**: Verified members can double-click any cell to edit it directly in the web UI, or use the "+ Add Row" modal. Changes are securely synced to Google Sheets via the backend and cache is invalidated automatically.
5. **Session Hardening**: Rolling 2-hour HTTP-only cookies, session fixation prevention via `cycle_key()`, single-use OAuth2 `state` verification, and CSRF protection on all write requests.

---

## 🚀 Features

- **Discord Role Screening**: Multi-role support (Guild Members, Alliance, etc.).
- **Interactive Web Spreadsheet**:
  - Double-click any cell to edit.
  - Press `Enter` to sync changes live to Google Sheets with visual feedback (green/red indicators).
  - Modal form to append new rows.
  - Client-side search and column filtering.
  - Manual cache refresh button (`?refresh=1`).
- **Offline Mock Development Mode**: Full local development workflow with simulated Discord roles and in-memory sheet editing without requiring live credentials.
- **Automated Test Suite**: 12 comprehensive unit and integration tests covering role enforcement, API operations, OAuth flows, and caching.
- **Render Ready**: Built-in `build.sh`, `render.yaml`, `gunicorn`, `whitenoise`, and automatic `RENDER_EXTERNAL_HOSTNAME` detection.

---

## ☁️ Deploying to Render (Recommended)

1. **Push your code to GitHub**.
2. Go to your [Render Dashboard](https://dashboard.render.com/) and click **New +** > **Web Service**.
3. Connect your GitHub repository.
4. Configure the settings:
   - **Environment**: `Python`
   - **Build Command**: `./build.sh`
   - **Start Command**: `gunicorn core.wsgi:application`
5. Under **Environment Variables**, add:
   - `DEBUG`: `False`
   - `SECRET_KEY`: (Click *Generate* or paste a random string)
   - `DISCORD_CLIENT_ID`: Your Discord application Client ID
   - `DISCORD_CLIENT_SECRET`: Your Discord application Client Secret
   - `DISCORD_REDIRECT_URI`: `https://<your-service-name>.onrender.com/auth/discord/callback/`
   - `DISCORD_BOT_TOKEN`: Your bot token
   - `DISCORD_TARGET_GUILD_ID`: Your Discord server ID
   - `DISCORD_GUILD_MEMBER_ROLE_ID`: Role ID allowed access
   - `DISCORD_ALLIANCE_ROLE_ID`: Secondary role ID (optional)
   - `DISCORD_INVITE_URL`: Server invite link (for unauthorized page)
   - `GOOGLE_SHEET_ID`: Your Google Sheet ID
   - `GOOGLE_CREDENTIALS_JSON`: The raw JSON string of your Google service account credentials
6. In the **Discord Developer Portal**, add your redirect URI to **OAuth2 > Redirects**:
   `https://<your-service-name>.onrender.com/auth/discord/callback/`
7. Click **Deploy Web Service**!

---

## 🛠️ Local Getting Started

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/DarkSun2003/<repo-name>.git
cd <repo-name>

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux / macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment configuration:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:
- **`DISCORD_CLIENT_ID` & `DISCORD_CLIENT_SECRET`**: From the [Discord Developer Portal](https://discord.com/developers/applications).
- **`DISCORD_REDIRECT_URI`**: E.g. `http://localhost:8000/auth/discord/callback/` for local dev.
- **`DISCORD_BOT_TOKEN`**: Bot token with Server Members intent enabled.
- **`DISCORD_TARGET_GUILD_ID`**: Your Discord server ID.
- **`DISCORD_GUILD_MEMBER_ROLE_ID` & `DISCORD_ALLIANCE_ROLE_ID`**: Role IDs permitted to view/edit.
- **`GOOGLE_SHEET_ID`**: The ID found in your Google Sheet URL (`/d/<SHEET_ID>/edit`).

### 3. Google Sheets Service Account Setup

1. In the [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Sheets API** and **Google Drive API**.
2. Create a **Service Account** and generate a JSON key.
3. Either:
   - Save the key file as `google_credentials.json` in the project root, or
   - Paste the JSON string into the `GOOGLE_CREDENTIALS_JSON` environment variable in `.env`.
4. Open your Google Sheet in Google Drive, click **Share**, and invite the service account email (e.g. `my-bot@project.iam.gserviceaccount.com`) as an **Editor**.

---

## 🧪 Testing

Run the automated test suite:

```bash
python manage.py test
```

All 12 unit and integration tests will execute against in-memory mocks.

---

## 💻 Local Offline Development (Mock Mode)

To test the application locally without real Discord OAuth or Google credentials, set in your `.env`:

```ini
DEBUG=True
MOCK_DISCORD_AUTH=True
MOCK_DISCORD_SCENARIO=allowed
```

Available scenarios:
- `allowed`: Simulates an authorized member with the required role.
- `unauthorized_role`: Simulates a member in the server who lacks the required role.
- `not_in_server`: Simulates an external user who has never joined the server.

---

## 📄 License

All rights reserved. Private project.
