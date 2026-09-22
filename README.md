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

`tests/test_registration_flow.py` runs the **whole form on a real dispatcher**
(`tests/harness.py` feeds real `Update` objects to the production routers while
the Telegram API is a recorder). It covers exactly the failures participants
reported: the tenant's own direction list, the four SPL Avtozvuk categories,
one tap per category, duplicate/failed photo updates, stale buttons, the
tenant-branded `/start`, the moderation card after a decision, and deleting an
application so it can be registered again.

**Manual end-to-end checklist** (needs a real token + channels + Google creds):

1. `/start` while **not** subscribed → prompted to subscribe; after subscribing,
   "Я подписался" starts the form.
2. Complete the form (country → plate → direction → 4 photos → modification
   photos → phone) → "Спасибо".
3. Moderation chat receives the photos (4 sides, then the modification close-ups)
   + a summary with Accept / Reject.
4. **Accept** → applicant gets "№1 …" plus the generated ticket; a new row (with
   the photos inline) appears in the Google Sheet. **Reject** → the applicant is
   told they did not pass; if the tenant has a «Дата для гостей» filled in, the
   text also invites them as a guest (SPL Show has none — participants only).
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
  Opening an application shows a **🗑 Удаление заявки / 🗑 Arizani o‘chirish**
  button: it deletes the row with its photos for good, so the person can run
  the form again (`/start`) and the freed registration number goes to the next
  approved application. This is what makes repeated tester runs possible —
  a rejected or approved application otherwise keeps answering `/start` with
  its old status.

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
   - Leave **Регистрация закрыта** unchecked. Registration is per tenant.
     A process-wide `REGISTRATION_CLOSED=true` secret is ignored, so it cannot
     make this bot answer «регистрация завершена».
   - Event fields (filled automatically for slug `splshow` if empty):
     - `event_date_text_ru`: `02 октября 2026 с 17:00 до 22:00` (participant entry)
     - `event_date_text_uz`: `02-oktyabr 2026, soat 17:00 dan 22:00 gacha`
     - `event_venue_text_ru/uz`: `Tashkent INDEX`
     - participant note: 03 октября 2026 с 09:00 рядом с автомобилем / 03-oktyabr 2026 soat 09:00 dan avtomobil yonida
     - `event_guest_date_text_ru/uz`: **empty** — the show is for registered
       participants only, so the rejection message carries no date/time at all.
       Type a guest date here to switch the guest invitation back on; the boot
       migration clears the old `03 октября 2026 с 12:00` but never a value the
       team entered on purpose.
   - Save → worker hot-restarts only `splshow`.

2. Directions CRUD: `/super-admin/tenants/splshow/directions`
   Seeding is automatic for an SPL tenant — no clicking needed:
   - Roots: `SQ`, `Выставка`, `Тюнинг`, `SPL Автозвук`.
   - `Тюнинг` → `Т1 Новичок`, `Т2 Профессионал`.
   - `SPL Автозвук` → the four categories the client confirmed:
     `SPL Front`, `SPL Тыл` (UZ `SPL Orqa`), `SPL Game (129/139/149)`,
     `SPL Sport / SPL Show`.
   Deployment that still holds the first placeholder seed (SPL / SPL Т1 /
   SPL Т2) is migrated to those four on the next boot; a list an admin edited
   by hand is never touched.
   Anything can be renamed/added here (max 2 levels); the bot always reads the
   tenant's own rows, so the buttons, the DB value and exports stay in sync.

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
- `pytest -q`: 171 passed (includes the end-to-end form on a real dispatcher)

## Reliability on event day

Everything below comes from the incidents of the 2–3 October launch. Each item
has a regression test.

| Symptom reported | Cause | Fix |
| --- | --- | --- |
| «Категории SPL Avtozvuk не актуальные», only 3 of them, wrong labels in Uzbek | `_load_tenant_directions` called `list_directions(tenant_id=…)` on the tenant-scoped facade, which does not accept it. The `TypeError` was swallowed by a broad `except`, so the bot fell back to the **legacy Promotors** list | The loader inspects the facade and asks the right question; a missing list is logged as a setup mistake instead of silently showing another event's categories |
| The bot froze after choosing a category ("загрузил второе фото — тишина") | `format_final_choice(directions, leaf_id=…)` was called with a signature it does not have → the handler crashed, aiogram only logged it | Helpers accept the leaf-list form; every step answers, stale/unknown buttons repeat the current question |
| «После одобрения приходят неправильные даты», «после /start письмо от Promotors с сентябрём» | `show_status()` was called without the tenant config, so the legacy Promotors template (September dates + `t.me/promotorsshow`) was used | Status answers always use the tenant config; the no-config fallback is event-neutral |
| «После принятия заявки в группе не изменился статус» | A panel decision never touched the Telegram card, and a failed `edit_text` was swallowed | The card message id is stored on the application; the decision is appended, buttons removed, and a fallback message is posted if editing fails. Panel decisions clear the card buttons too |
| «Бот зависает» / «qotyapti» | Any handler exception left the participant without an answer; restarts wiped the FSM | `bot/errors.py` answers callbacks (alert) and private chats (technical-error note); `bot/services/fsm_storage.py` keeps states in SQLite so a restart/hot reload no longer drops a half-finished form |
| «Удалить зарегистрированного человека из базы нельзя» | There was no delete action | 🗑 delete on the application page removes the row and its photos; the person can register again and the number is reused |
| «Не пришла сгенерированная картинка после одобрения» | The ticket was rendered and sent in one `try`, and any failure was only logged: no image, no explanation, no way to resend | Delivery is layered — PNG → JPEG (when Telegram refuses the photo) → file → the ticket is posted into the moderation chat with "forward it to the participant"; the render runs in a thread with a timeout and is retried without the hero photo. The team can resend it themselves: `/ticket 123` in the moderation chat, or the 🎫 button on the application page |
| «При отклонении заявки приходит сообщение с неправильным времени мероприятия» | The rejection invited the person as a guest with a date/time that was wrong (September copy on the deployed build), and a named hall was described as a parking lot | The guest invitation now appears **only** when the tenant has a «Дата для гостей» — SPL has none, so its rejection carries no date, time or venue; Promotors keeps its guest invitation word for word. The venue wording also separates a parking lot from a named venue (RU «на площадке», UZ «manzilida») |
| «Bot sekin rasm yuklash qismida birinchi rasmni yuklagandan keyin osilib qolyapti» / «бот очень медленно»: the photo step hung after the first photo | The photo step saved the bytes **before** answering: the download ran inside the update, the update ran inside the per-user lock, so a slow Telegram CDN held back the participant's next message (0.78 s per photo, 3.2 s for an album, and nothing at all behind a stalled stream). The direction banner was re-uploaded from the volume on every registration | Each photo is answered first and downloaded behind the answer (`bot/services/media.py::PhotoIngest`, one task per photo, 15 s cap, atomic write): 0.78 s → 0.28 s per photo, 3.2 s → 1.1 s for a four-photo album, and nothing the participant sends waits for a stalled download. A failed side stays reserved — the announced resend refills that exact slot — and a side missing from the volume is re-fetched from its `file_id` before the form is accepted; the banner is uploaded once per tenant and re-sent as `file_id` after that |
| The arrival line read «Заезд авто участников: …» | The client's own wording for it is «Заезд участников — 2 октября» | The branded approval says «Заезд участников — <b>{дата}</b>» (UZ «Ishtirokchilar kirishi — …»), taken from the tenant's event date |
| «Неправильные даты и время» on a participant's poster / a self-test ticket that shows another event's schedule | A tenant-branded ticket took the tenant's date and then filled the missing half from the base copy — a tenant with a date but no venue printed **SOF EXPO · SAMARKAND**, and one with neither printed Promotors' «11 сентября». The `/diag` self-test rendered without the tenant at all, so the team saw the same foreign dates | `_resolve_ticket_copy` uses the tenant's own date and venue and nothing else (a blank part is simply left out of the «date • place» line); `/diag` renders through the same tenant-branded path as the real ticket; the panel's event placeholders no longer suggest September values. Every participant-facing approval/rejection/status message already goes through `*_for_tenant` |
| Testers tap the status button or `/start` in the middle of the form, get «у вас нет заявки» and fill everything in again | A half-filled form was treated as "no registration": the status answer told them to `/start`, and `/start` silently wiped the collected photos | Mid-form the status button continues the form (current step re-asked); `/start` offers «Продолжить / Начать заново» instead of deleting the answers; when nothing is found the log names the tenants that do hold rows for that person |

Operational notes:

- States live in `fsm_storage` inside the same SQLite file (`DB_PATH`); rows
  untouched for 30 days are dropped on startup. A broken database degrades to
  in-memory storage instead of stopping the bot.
- Every failure is logged with the tenant slug and update id — search the log
  for `"failed:"` when a participant reports a silent bot.
- The photo ledger is keyed by the worker's **identity**: aiogram's `Bot`
  compares equal to any other `Bot` with the same token, so a ledger keyed by
  the object itself would follow a hot-reloaded worker into its replacement
  (stale "this side failed" entries decide which slot the next photo fills).
- Photos: an update is answered before its photo is on disk; the resend asks
  («⚠️ Не удалось сохранить фото (…). Пришлите его, пожалуйста») name the side
  once the download really fails, and the form refuses to be accepted while a
  photo is unaccounted for — it waits for a fetch in flight, silently re-fetches
  a side that is missing from the volume, and only then asks the participant
  again (`PHOTO_RESEND_BEFORE_FINISH`).
- Tickets: Telegram's photo limit is 10 MB, so a poster above ~9 MB is sent as
  JPEG from the start; if a send fails the moderation chat receives the same
  image with a "forward this to the participant" caption.
- Re-registration for testing: delete the application (🗑 on its page) — the
  person and the number are freed immediately. `/start` then runs the form
  again from the beginning.

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
