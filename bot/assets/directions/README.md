# Direction banners

When a participant picks a direction, the bot sends that direction's promo
banner so they see the category they just joined.

## Easiest way: send them to the bot (no git needed)

In the moderation chat, send the image **with a caption**:

```
/banner drift
```

Uploads are stored on the Fly volume, so they survive restarts and redeploys.
Send as a **file** (uncompressed) for the best quality.

- `/assets` — see what's currently loaded
- `/delasset banner drift` — remove one
- `/help_assets` — short reminder of the commands

## Slug per direction

The slug comes from the `DIRECTIONS` table in `bot/constants.py` — the single
place a direction is defined. Current slugs:

| Direction        | Slug / filename |
| ---------------- | --------------- |
| SPL Тюнинг       | `tuning`        |
| SPL Автозвук     | `autosound`     |
| Adrenaline Drift | `drift`         |
| Ретро            | `retro`         |

Renaming a direction is a one-line change in that table;
the button labels, the stored value, the exports and this filename all follow
from it.

## Alternative: commit them here

Drop `<slug>.png` (or `.jpg` / `.jpeg` / `.webp`) in this folder and redeploy.
Files uploaded through the bot take priority over anything committed here.

Notes:
- Square (1:1) images look best in Telegram; the sponsor logos are already
  baked into the banners themselves.
- A missing banner simply means none is sent for that direction — the
  registration flow continues normally, nothing breaks.
