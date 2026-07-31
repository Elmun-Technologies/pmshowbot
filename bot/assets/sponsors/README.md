# Sponsor / partner logos

These logos are shown in a single row **across the very top of the ticket** —
the same header-strip layout used on the event's own promo artwork.

## The four partner logos for this event

The strip is built around these four, in this order. Use exactly these names so
the order and the `/assets` checklist line up:

| Slot | Name | Logo |
| --- | --- | --- |
| 1 | `1_mcs_sherdor` | Мотоклуб MCS «Sherdor» (Самарканд) |
| 2 | `2_retro_tashkent` | Авто-Ретро Клуб (Ташкент) |
| 3 | `3_drift_show` | Uzbekistan Drift Show |
| 4 | `4_sof_expo` | SOF EXPO Samarkand |

`/assets` in the moderation chat shows which of the four are already in place
and which are still missing, with the exact command to send for each.

## Easiest way: send them to the bot (no git needed)

In the moderation chat, send the image **with a caption**:

```
/logo 1_mcs_sherdor
```

The number at the start sets the order: `1_`, `2_`, `3_`, `4_`… Send it as a
**file** (uncompressed) for the best quality. Any extra sponsor beyond the four
above just gets its own name (`5_partner`, …) and joins the end of the strip.

Uploads are stored on the Fly volume, so they survive restarts and redeploys.

- `/assets` — see what's currently loaded
- `/delasset logo 1_pride` — remove one
- `/help_assets` — short reminder of the commands

## Alternative: commit them here

Drop `.png` / `.jpg` files in this folder (named as in the table above) and
redeploy. Files uploaded through the bot take priority over anything committed
here — as soon as one logo is uploaded, the uploaded set replaces the committed
set entirely, so upload all four rather than mixing the two sources.

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
