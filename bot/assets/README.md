# Assets

Drop optional brand assets here — commit them (they're not gitignored) so
they're baked into the Docker image on the next deploy:

- `logo.png` — the PROMOTORS SHOW · Samarkand logo (transparent PNG, wide,
  ideally ~1200px wide). If present, it is composited on the ticket poster
  instead of the typographic wordmark.
- `adrenaline.png` — the Adrenaline Rush co-brand logo, shown alongside
  `logo.png` at the top of the ticket.
- `sponsors/*.png` — additional partner/sponsor logos shown in a strip near
  the bottom of the ticket. See `sponsors/README.md` for naming and details.
- `fonts/display.ttf`, `fonts/text.ttf`, `fonts/script.ttf` — optional font
  overrides for the ticket (fall back to DejaVu if absent).
