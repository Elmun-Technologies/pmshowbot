# Sponsor / partner logos

Drop each partner's logo here as a `.png` or `.jpg` file. The ticket
automatically shows every logo found in this folder in a "ПРИ ПОДДЕРЖКЕ" /
"HAMKORLAR" strip near the bottom — no code change needed.

- Logos are shown in **filename order**, so prefix with a number to control
  the order, e.g. `1_spl_show.png`, `2_acv.png`, `3_tuning_ibragimov.png`.
- Any background works (white, red, transparent, etc.) — each logo is placed
  on its own white card in the strip, auto-cropped to its content and scaled
  to a consistent height. Transparent PNGs look cleanest.
- Up to 6 logos are shown; if there's no room at the ticket's width they
  shrink automatically to fit.
- No logos here → the strip is simply omitted (nothing breaks).

After adding/removing files: `git add bot/assets/sponsors/*.png && git commit`
then redeploy (`fly deploy`).
