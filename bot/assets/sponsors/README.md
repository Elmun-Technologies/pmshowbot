# Sponsor / partner logos

Drop each partner's logo here as a `.png` or `.jpg` file. The ticket
automatically shows every logo found in this folder in a single row **across
the very top of the ticket** — the same header-strip layout used on the
event's own promo artwork.

- Logos are shown in **filename order**, so prefix with a number to control
  the order, e.g. `1_pride.png`, `2_tuning_ibragimov.png`, `3_acv.png`,
  `4_spl_show.png`.
- **Transparent PNGs look best**: logos are placed straight on the ticket's
  dark background with thin vertical dividers between them, exactly like the
  promo banners. A logo that has its own solid background (red, white, …)
  still works — it just renders as its own coloured block, as it does on the
  banners.
- Each logo is auto-cropped to its content and scaled to a consistent height;
  if the row would be too wide, every logo shrinks together until it fits.
- Up to 6 logos are shown.
- No logos here → the strip is simply omitted (nothing breaks); the event
  branding (`logo.png` + `adrenaline.png`) then sits at its normal position.

After adding/removing files: `git add bot/assets/sponsors/*.png && git commit`
then redeploy (`fly deploy`).
