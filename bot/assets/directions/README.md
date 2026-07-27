# Direction banners

When a participant picks a direction, the bot sends that direction's promo
banner so they see the category they just joined.

Drop one image per direction here, named **exactly** as below (`.png`, `.jpg`,
`.jpeg` or `.webp` all work):

| Direction (Тюнинг / Автозвук / Дрифт / Ретро) | Filename          |
| --------------------------------------------- | ----------------- |
| Тюнинг                                         | `tuning.png`      |
| Автозвук                                       | `autosound.png`   |
| Дрифт                                          | `drift.png`       |
| Ретро                                          | `retro.png`       |

Notes:
- Square (1:1) images look best in Telegram; the sponsor logos are already
  baked into the banners themselves.
- A missing file simply means no banner is sent for that direction — the
  registration flow continues normally, nothing breaks.
- Telegram caps photos at 10 MB; keep each banner well under that.

After adding files: `git add bot/assets/directions/*.png && git commit`, then
redeploy (`fly deploy`).
