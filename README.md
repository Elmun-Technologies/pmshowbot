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
python -m pytest -q       # full suite (db, admin panel, i18n, tenants, …)
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

## Panel languages (RU / O‘Z)

The whole admin panel — super-admin and tenant sides, including all login
pages — works in **Русский** and **O‘zbekcha**. Russian (`ru`) is the default,
so a client who does not read Uzbek can use the panel entirely in Russian.

**Switching languages.** Every page header (and every login page) shows a
`RU | O‘Z` switcher. Click `O‘Z` and the interface — navigation, tables,
buttons, forms, validation and flash messages, exports' CSV headers, confirm
dialogs, empty states and 404 texts — renders in Uzbek immediately. The choice
is stored in a separate `pm_lang` cookie (`HttpOnly`, `SameSite=Lax`, `Secure`,
1 year) and kept across pages and sessions. You can also switch (or deep-link)
with the `?lang=ru` / `?lang=uz` query parameter — a login POST or redirect
keeps the choice.

**Security properties (unchanged by locale):**

- The locale lives in its own cookie and never touches the tenant or
  super-admin session cookies; `?lang=` cannot log you in, log you out, or
  change which tenant's data you see.
- Only `ru` and `uz` are accepted. Anything else in the cookie or query string
  (e.g. `?lang=fr` or a crafted value) safely renders the Russian default
  without being reflected into the HTML — no XSS, no open redirect.
- Bot tokens are never rendered in any language: the panel shows only the
  `***` configured marker.
- The switcher link is relative (`?lang=…`), so it works identically on
  `/super-admin/…` and tenant-scoped `/t/<slug>/…` URLs and keeps list filters
  (status/search) when switching on the applications page.

**Where translations live.** All panel strings are centralized in
`bot/admin/i18n.py` as two flat dictionaries keyed by stable dot-paths
(e.g. `t(lang, "tenant.create.title")`). Unknown keys and unknown locales fall
back to Russian; adding a new UI string means adding one `ru` and one `uz`
entry there — views never contain `if lang == ...` branches. The Telegram-side
admin/moderation texts (`MODERATION_*`, `/diag`, `/stats`, … in
`bot/texts.py` / `bot/handlers/moderation.py`) intentionally stay
Russian-only, matching the default panel locale, so the participant flows are
untouched.

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

## SPL Show tenant-branding (SPL) — multi-tenant SaaS guide

This release adds **SPL Show** as a first-class example of a fully tenant-branded event on top of the multi-tenant platform. **Promotors remains the default tenant**; SPL proves that a new tenant starts empty and can be branded without leaking Promotors assets.

### What is tenant-branded?

- **Tenant name** comes from `tenants.name` (`TenantConfig.tenant_name`), not hard-coded.
- **Channel link** comes from `tenants.channel_url` / `required_channel` — no hard-coded `t.me/promotorsshow` in new helpers.
- **Event dates / venue** come from new columns `event_date_text_ru/uz`, `event_venue_text_ru/uz`, `event_guest_date_text_ru/uz` (RU/UZ). Empty means sentence omitted.
- **Ticket**: 1-2 generic logo slots. Promotors keeps `logo.png` / `adrenaline.png` fallback from repo; other tenants have **no repo fallback** → wordmark from `tenant_name`. Generic slot titles are i18n (`assets.brand.generic_title`). Preview = real ticket logic.
- **Sponsors**: `PARTNER_LOGOS` checklist only for promotors or removed; bundled `bot/assets/sponsors/` fallback only for promotors; new tenants start empty + admin empty-state i18n (`assets.sponsors.empty_tenant`, `assets.partners.empty_tenant`).
- **Directions**: tenant-specific + podnapravleniya (2-level). New table `directions(id, tenant_id, parent_id, canonical, label_ru, label_uz, slug, sort_order, is_active, created_at, updated_at)`. Seed promotors 4 directions. Backward-compatible: old `applications.direction` canonical strings stay, new `direction_id` FK added. Storage format `Parent — Child` or `direction_id`. Admin CRUD at `/super-admin/tenants/{slug}/directions`. Export shows final name, `direction_label` stays.

### Architecture

- `bot/db.py`: tenants event columns + directions table, migrations additive only, `list_directions`, `create_direction`, `update_direction`, `delete_direction` (soft), seed promotors.
- `bot/config.py`: `TenantConfig` extended with 6 event fields, `tenant_config()` maps from Tenant.
- `bot/services/assets.py`: `_is_default_scope()` helper — bundled fallback only for `promotors` or legacy `None` scope. `sponsor_files`, `direction_banner`, `brand_logo` only fallback for promotors, `partner_status` returns `[]` for non-promotors, inventory respects scope.
- `bot/services/directions.py` (new): `build_hierarchy`, `format_final_choice`, `localized_final_choice`, `find_by_*` — 2-level logic, storage `Parent — Child`.
- `bot/keyboards.py`: `CB_SUB_DIRECTION`, `direction_keyboard_from_db()` uses DB ids + localized labels, legacy `direction_keyboard()` kept as fallback.
- `bot/states.py`: added `Registration.sub_direction`.
- `bot/texts.py`: tenant-branded helpers `greeting_for_tenant`, `subscribe_required_for_tenant`, `approved_for_tenant` (uses tenant channel_url), `rejected_for_tenant` (guest date + venue from tenant), `registration_closed_for_tenant`, `registration_closed_bilingual_for_tenant`, `share_cta_for_tenant`, `ticket_copy_for_tenant`. No hard-coded channel/date.
- `bot/handlers/registration.py`: loads directions from DB, parent→child flow storing `Parent — Child` + `direction_id`, tenant-branded greetings/closed.
- `bot/services/decisions.py`, `bot/services/ticket.py`: ticket uses tenant_config for date/venue branding, wordmark guarantee.
- `bot/admin/i18n.py`: new keys RU+UZ for directions CRUD, event fields, generic brand titles, empty-state.
- `bot/admin/views.py`, `bot/admin/server.py`: `/super-admin/tenants/{slug}/directions` CRUD, event field inputs i18n, generic brand titles, empty-state i18n, preview = real logic.

### SPL setup guide (slug: `splshow`)

1. Super-admin login `/super-admin/login` → **➕ Создать tenant**
   - Slug: `splshow`
   - Name: `SPL Show`
   - Bot token: from @BotFather
   - Admin chat ID, required channel `@splshow` (or yours), channel URL `https://t.me/splshow`, Instagram etc.
   - Event fields:
     - `event_date_text_ru`: `11 сентября 2026 с 10:00 до 19:00`
     - `event_date_text_uz`: `11-sentyabr 2026, 10:00 dan 19:00 gacha`
     - `event_venue_text_ru/uz`: `SOF EXPO`
     - `event_guest_date_text_ru`: `12 и 13 сентября с 10:00`
     - `event_guest_date_text_uz`: `12 va 13-sentyabr, 10:00 dan`
   - Save → worker hot-restarts only `splshow`.

2. Directions CRUD: `/super-admin/tenants/splshow/directions`
   - Add root: `SPL Тюнинг`, label RU `SPL Тюнинг`, UZ `SPL Tyuning`, slug `spl_tuning`, sort 0
   - Add children under it: `SPL Тюнинг — Show`, `SPL Тюнинг — Street`, etc. (parent = SPL Тюнинг). Max 2 levels.
   - Repeat for `Adrenaline Drift`, `Retro`, `Moto` or your own.

3. Ticket branding: `/t/splshow/ticket-assets`
   - Upload brand logos (generic slots) — transparent PNG ~1200px. If none uploaded, ticket shows wordmark `SPL SHOW`.
   - Upload sponsor logos: `1_spl_partner`, `2_local_garage` etc. No repo fallback — empty state says repo not used.
   - Preview shows real logic with your logos + tenant name.

4. Bot texts: automatically use tenant name/channel/date/venue from DB. Promotors texts preserved via seed/migration.

5. Test isolation:
   - `pytest` includes `test_assets` isolation, `test_directions`, `test_tenants`.
   - Manual: `/start` in `@splshowbot` → direction list shows only splshow directions, not promotors.

### File list (SPL deliverable)

- `bot/db.py`: directions table + event columns + CRUD + seed
- `bot/config.py`: TenantConfig event fields
- `bot/services/assets.py`: tenant isolation
- `bot/services/directions.py`: NEW hierarchy helpers
- `bot/keyboards.py`: DB direction keyboards + sub_direction
- `bot/states.py`: sub_direction state
- `bot/texts.py`: tenant-branded helpers
- `bot/handlers/registration.py`: DB directions + 2-level flow
- `bot/services/decisions.py`: tenant-branded approved/rejected/share
- `bot/services/ticket.py`: tenant date/venue + wordmark guarantee + preview=real
- `bot/handlers/mynumber.py`: tenant-branded status
- `bot/admin/i18n.py`: RU+UZ new keys
- `bot/admin/views.py`: directions CRUD pages, event inputs, generic brand titles, empty-state
- `bot/admin/server.py`: directions routes + event form values + preview real logic
- `tests/test_directions.py`: (existing + isolation)
- `tests/test_assets.py`: isolation (no repo fallback for non-promotors)

### Test results

- `py_compile` all edited files: OK
- `pytest -q`: 78 passed

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
