# Promotors Show Samarkand — registration bot

Telegram bot for registering cars for the **Promotors Show Samarkand** event.

**Flow:** `/start` (with a channel-subscription gate) → country → license plate →
4 car photos (left / right / front / back) → direction → phone number → "Thank
you". Each finished application is sent to a moderation chat with **Accept /
Reject** buttons. On approval the applicant gets a sequential registration number
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

## 3. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | What it is |
| --- | --- |
| `BOT_TOKEN` | Token from @BotFather |
| `REQUIRED_CHANNEL` | `@promotorsshow` (subscription gate) |
| `ADMIN_CHAT_ID` | Moderation chat id (numeric, usually negative) |
| `GOOGLE_CREDENTIALS_FILE` | Path to the service-account JSON (`credentials.json`) |
| `SPREADSHEET_ID` | Google Sheet id (leave empty to disable Sheets export) |
| `DRIVE_FOLDER_ID` | Drive folder id for photos (leave empty to disable upload) |

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
2. Complete the form (country → plate → 4 photos → direction → phone) → "Спасибо".
3. Moderation chat receives the 4 photos + summary with Accept / Reject.
4. **Accept** → applicant gets "№1 …"; a new row (with 4 inline photos) appears in
   the Google Sheet. **Reject** → applicant gets the guest invitation.
5. "Узнать свой номер" re-shows the applicant's status.

## Admin web panel

The bot process also serves a small web admin panel (same app, same SQLite DB).
Set `ADMIN_PASSWORD` to enable it; leave it empty to disable. On Fly it's
reachable at `https://<app>.fly.dev` (e.g. `https://pmshowbot.fly.dev`).

Features:
- **Login** with the single shared `ADMIN_PASSWORD` (signed-cookie session).
- **Dashboard** — totals (pending / approved / rejected), last issued number,
  and breakdowns by direction and country.
- **Applications** — list with photo thumbnails, filter by status, search by
  plate / phone / user; a detail page shows all four photos.
- **Approve / Reject** from the panel — runs the exact same logic as the Telegram
  buttons (assigns the number, messages the applicant, exports to Google if enabled).
- **Export CSV** (`/export.csv`, opens cleanly in Excel).

Locally it listens on `PORT` (default `8080`): open `http://localhost:8080`.
The panel is exposed publicly on Fly, so use a strong password (it is the only
gate). Traffic is HTTPS on Fly (`force_https`).

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

# 3. Create the persistent volume (same region as fly.toml's primary_region)
fly volumes create pmshow_data --region ams --size 1 -a pmshowbot

# 4. Set secrets (do NOT put these in fly.toml)
fly secrets set -a pmshowbot \
  BOT_TOKEN="123456:ABC..." \
  REQUIRED_CHANNEL="@promotorsshow" \
  ADMIN_CHAT_ID="-1001234567890" \
  ADMIN_PASSWORD="<strong password for the web panel>"
# For a demo without the subscription gate: also set REQUIRE_SUBSCRIPTION="false"
# For Google export later: fly secrets set GOOGLE_CREDENTIALS_JSON="$(cat credentials.json)" \
#   SPREADSHEET_ID="..." DRIVE_FOLDER_ID="..." -a pmshowbot

# 5. Deploy, then ensure exactly ONE machine runs (two would conflict on polling)
fly deploy -a pmshowbot
fly scale count 1 -a pmshowbot

fly logs -a pmshowbot     # watch it start
```

Notes:
- No `[http_service]` / port — the machine stays always-on and is not health-checked.
- `DB_PATH` / `MEDIA_DIR` point at the mounted volume so data and registration
  numbers survive restarts and redeploys.
- Run **only one** machine: a second instance would also call Telegram
  `getUpdates` and cause a conflict.

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
