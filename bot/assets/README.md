# Assets

Drop optional brand assets here:

- `logo.png` — the PROMOTORS SHOW · Samarkand logo (transparent PNG, wide,
  ideally ~1200px wide). If present, it is composited on the ticket poster
  instead of the typographic wordmark.
- `fonts/display.ttf`, `fonts/text.ttf`, `fonts/script.ttf` — optional font
  overrides for the ticket (fall back to DejaVu if absent).

These files are gitignored by default (binaries); commit them explicitly if you
want them baked into the Docker image, or mount them via a Fly volume.
