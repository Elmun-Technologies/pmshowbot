# Sponsor / partner logos

These logos are shown in a single row **across the very top of the ticket** —
the same header-strip layout used on the event's own promo artwork.

## Easiest way: send them to the bot (no git needed)

In the moderation chat, send the image **with a caption**:

```
/logo 1_pride
```

The number at the start sets the order: `1_`, `2_`, `3_`, `4_`… Send it as a
**file** (uncompressed) for the best quality.

Uploads are stored on the Fly volume, so they survive restarts and redeploys.

- `/assets` — see what's currently loaded
- `/delasset logo 1_pride` — remove one
- `/help_assets` — short reminder of the commands

## Alternative: commit them here

Drop `.png` / `.jpg` files in this folder and redeploy. Files uploaded through
the bot take priority over anything committed here.

## Design notes

- **Transparent PNGs look best**: logos sit straight on the ticket's dark
  background with thin vertical dividers between them. A logo with its own
  solid background (red, white, …) still works — it just renders as its own
  coloured block, as it does on the banners.
- Each logo is auto-cropped to its content and scaled to a consistent height;
  if the row would be too wide, every logo shrinks together until it fits.
- Up to 6 logos are shown.
- No logos at all → the strip is simply omitted; the event branding
  (`logo.png` + `adrenaline.png`) then sits at its normal position.
