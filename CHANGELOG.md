# Changelog

## 0.1.0

- `margin sync` publishes repository markdown to Outline as nested documents, with small verified patches that keep comments attached.
- Edits made in Outline, and changes on both sides, are reported and never overwritten.
- `margin serve` receives signed comment webhooks, runs a catch-up poll, and delivers comments in batches after a quiet period.
- `margin reply`, `resolve`, `thread` and `status`.
