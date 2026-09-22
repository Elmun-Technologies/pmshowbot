# Promotors Show — multi-tenant registration platform

A professional multi-tenant Telegram registration platform. **Promotors Show** remains the default migrated tenant; Adrenaline, Drift 2026, and future events each get their own Telegram bot, moderation chat, channel, data, assets, and admin password.

**Flow:** `/start` (with a channel-subscription gate) → country → license plate →
direction → 4 car photos (left / right / front / back) → photos of what was
changed on the car (hood, trunk, audio, … — optional, up to 6) → phone number →
"Thank you". Each finished application is sent to a moderation chat with
**Accept / Reject** buttons. On approval the applicant gets a sequential registration number
and a confirmation message, and the full application — with photos embedded via
Google Drive — is appended to a Google Sheet. On rejection they get a guest
invitation. An **"Узнать свой номер"** button lets people re-check their status.

## Stack

- Python 3.11+, [aiogram 3](https://docs.aiogram.dev/) (long polling)
- SQLite (applications + registration-number counter)
- `gspread` + Google Drive API (Sheets export with inline photos)

## 1. Create the Telegram bot

1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the **token**.
2. Create a **moderation chat** (a private group or channel) where applications
   land. Add the bot to it and make the bot an **administrator**. Get its numeric
   chat id (e.g. forward a message from it to [@getidsbot](https://t.me/getidsbot),
   or check the id — it's usually negative, like `-1001234567890`).
3. Make the bot an **administrator** of the subscription channel `@promotorsshow`
   (required so it can check who is subscribed).

## 2. Set up Google (service account)

The bot writes to Google with its **own** service account — you don't use a
personal Google login at runtime.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a
   project (free).
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google
   Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   Create it, then open it → **Keys → Add key → JSON** → download the file and
   save it as `credentials.json` in the project root.
4. Copy the service account's email (looks like
   `something@your-project.iam.gserviceaccount.com`).
5. Create a **Google Sheet** → **Share** it with that email as **Editor**. Copy
   the spreadsheet id from its URL:
   `docs.google.com/spreadsheets/d/`**`<SPREADSHEET_ID>`**`/edit`.
6. Create a **Drive folder** for the photos → **Share** it with the same email as
   **Editor**. Copy the folder id from its URL:
   `drive.google.com/drive/folders/`**`<DRIVE_FOLDER_ID>`**.

## 3. Configure the platform

```bash
cp .env.example .env
```

Only process-wide secrets belong in `.env` now:

| Variable | What it is |
| --- | --- |
| `SUPER_ADMIN_PASSWORD` | Password for `/super-admin/login` (creates/manages all tenants) |
| `ENCRYPTION_KEY` | Persistent Fernet key that encrypts tenant bot tokens in SQLite |
| `GOOGLE_CREDENTIALS_FILE` / `GOOGLE_CREDENTIALS_JSON` | Shared service-account credentials; each tenant has its own Sheet/Drive IDs |
| `DB_PATH`, `MEDIA_DIR`, `PORT` | Persistent platform data and HTTP panel settings |

Generate the encryption key **once** and store it as a deployment secret:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### One-time Promotors migration

On the first startup the app creates the `promotors` tenant and copies optional
legacy `BOT_TOKEN`, `ADMIN_CHAT_ID`, `REQUIRED_CHANNEL`, `SPREADSHEET_ID`,
`DRIVE_FOLDER_ID`, Instagram and `ADMIN_PASSWORD` values into it. Existing
applications and bot users are linked to that tenant; old runtime logos move
from `media/_sponsors`, `_brand`, `_directions` to
`media/_tenants/promotors/...`. Keep the legacy variables only for this first
migration; all subsequent tenant settings are managed in the browser.

### Create additional tenants

1. Open `https://<app>/super-admin/login` with `SUPER_ADMIN_PASSWORD`.
2. Create a tenant (for example `adrenaline`) and enter its bot token,
   moderation chat, required channel, integrations, and tenant-admin password.
3. Use **Diagnostics** to validate the token, channel admin permission and
   moderation chat. The worker hot-restarts only that tenant.
4. Tenant staff use `/login` or `/t/adrenaline/login`. They can see only their
   own applications, broadcasts, exports, settings, and ticket artwork.

## 4. Install & run

```bash
pip install -r requirements.txt

# Validate config + Google access (no Telegram needed):
python -m bot.config --check

# Run the bot:
python -m bot.main
```

## Testing

```bash
python tests/test_db.py   # sequential registration numbers, status transitions
```

**Manual end-to-end checklist** (needs a real token + channels + Google creds):

1. `/start` while **not** subscribed → prompted to subscribe; after subscribing,
   "Я подписался" starts the form.
2. Complete the form (country → plate → direction → 4 photos → modification
   photos → phone) → "Спасибо".
3. Moderation chat receives the photos (4 sides, then the modification close-ups)
   + a summary with Accept / Reject.
4. **Accept** → applicant gets "№1 …"; a new row (with the photos inline) appears
   in the Google Sheet. **Reject** → applicant gets the guest invitation.
5. "Узнать свой номер" re-shows the applicant's status.

## Admin web panel

The process serves one HTTPS control plane on `PORT` (default `8080`). It has
two deliberately separate roles:

- **Super admin** — `/super-admin/login`, protected by
  `SUPER_ADMIN_PASSWORD`. Can create/edit/activate/archive tenants, enter bot
  tokens (stored Fernet-encrypted), run diagnostics and view cross-tenant
  application counts.
- **Tenant admin** — `/login` chooses a tenant, or use `/t/<slug>/login`.
  The per-tenant password is PBKDF2-hashed in SQLite. Its signed cookie is
  separate from the super-admin cookie, and every dashboard, application,
  broadcast, export, asset and settings query is server-scoped to that tenant.

Each tenant manages its own ticket assets at `/t/<slug>/ticket-assets`. Runtime
files live under `media/_tenants/<slug>/_sponsors`, `_brand`, and `_directions`;
new participant photos are stored below the same tenant root. A tenant's
sponsor logos, banners and ticket preview can never be read through another
tenant's panel URL.

## Ticket design & sponsor logos

The generated ticket carries a strip of partner logos across its top. The four
the strip is built around are:

| Slot | Upload as | Logo |
| --- | --- | --- |
| 1 | `/logo 1_mcs_sherdor` | Мотоклуб MCS «Sherdor» (Самарканд) |
| 2 | `/logo 2_retro_tashkent` | Авто-Ретро Клуб (Ташкент) |
| 3 | `/logo 3_drift_show` | Uzbekistan Drift Show |
| 4 | `/logo 4_sof_expo` | SOF EXPO Samarkand |

Each tenant can manage its ticket design **directly from its scoped admin panel** at `/t/<slug>/ticket-assets`:

- Live ticket preview (`/ticket-assets/preview.png`) with current logos.
- Upload / delete **brand logos** (`logo.png`, `adrenaline.png`) that sit on top of the poster.
- Add / remove **sponsor / homiylar logos** — filename controls order (`1_`, `2_`, …), transparent PNG recommended.
- Manage **direction banners**.

The old way still works: send each logo to the moderation chat **as a file** with that caption — uploads
land on the Fly volume and appear on tickets immediately, no redeploy needed.
`/assets` shows which of the four are still missing, `/diag` renders a test
ticket. They can also be committed to `bot/assets/sponsors/` under the same
names — see [`bot/assets/sponsors/README.md`](bot/assets/sponsors/README.md).

## Editing wording / dates

All user-facing text lives in `bot/texts.py` (greeting, approval/rejection
messages, dates, button labels, country/direction lists). Change it there.

## Deployment (persistent host)

Run it anywhere that stays online (a small VPS is enough). The bot uses long
polling, so **no public URL / webhook is needed**.

### systemd

Copy `deploy/pmshowbot.service` to `/etc/systemd/system/`, edit the paths/user,
then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pmshowbot
sudo journalctl -u pmshowbot -f
```

### Fly.io

The bot is a long-polling **worker** (no inbound HTTP), so deploy it with
`flyctl` using the included `fly.toml` — **not** the "Launch from GitHub" web UI
(that assumes a web app with a port and won't create the data volume).

```bash
# 1. Install flyctl and log in
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. Create the app (pick a unique name; update `app` in fly.toml to match)
fly apps create pmshowbot

# 3. Create the persistent volume (same region as fly.toml's primary_region).
#    REQUIRED before the first deploy, or the deploy fails (fly.toml expects it).
fly volumes create pmshow_data --region ams --size 3 -a pmshowbot

# 4. Set secrets (do NOT put these in fly.toml)
fly secrets set -a pmshowbot \
  SUPER_ADMIN_PASSWORD="<strong platform password>" \
  ENCRYPTION_KEY="<persistent Fernet key>"
# Optional one-time migration values can be supplied on the first deploy:
# BOT_TOKEN="..." ADMIN_CHAT_ID="..." REQUIRED_CHANNEL="..." ADMIN_PASSWORD="..."
# For a demo without the subscription gate: also set REQUIRE_SUBSCRIPTION="false"
# For the ticket's Instagram CTA: also set INSTAGRAM_HANDLE="promotorsshow"
# For Google export: fly secrets set GOOGLE_CREDENTIALS_JSON="$(cat credentials.json)" -a pmshowbot
# Then configure each tenant's Spreadsheet ID and Drive folder in /super-admin.

# 5. Deploy, then ensure exactly ONE machine runs (two would conflict on polling)
fly deploy -a pmshowbot
fly scale count 1 -a pmshowbot

fly logs -a pmshowbot     # watch it start
```

Notes:
- The included `[http_service]` exposes the tenant/super-admin panel and keeps
  one machine alive for polling.
- `DB_PATH` / `MEDIA_DIR` point at the mounted volume so tenant data, artwork,
  photos and registration numbers survive restarts and redeploys.
- Run **only one** machine: a second instance would duplicate every active
  tenant's `getUpdates` stream.

### Docker

```bash
docker build -t pmshowbot .
docker run -d --name pmshowbot --restart unless-stopped \
  --env-file .env \
  -v "$PWD/credentials.json:/app/credentials.json:ro" \
  -v "$PWD/data:/app/data" \
  -v "$PWD/media:/app/media" \
  pmshowbot
```

## Notes

- Single-instance bot; FSM state is in memory, so an in-progress form resets if
  the process restarts (completed applications are safely stored in SQLite).
- `data/` (SQLite) and `media/` (downloaded photos) persist runtime data — keep
  them on a persistent volume. Both are gitignored.
