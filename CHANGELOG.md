# Changelog

## 0.2.0

- Excluded folders are skipped during the walk, and non-markdown names are skipped before pattern matching, so a large root costs little.
- `margin link FILE...` publishes files at once and prints a Markdown link to each, for an agent to hand to the person.
- `margin setup-claude` links the skill, registers Outline's MCP with the key read at connect time, and denies its destructive tools.
- One writer per repository across processes, so a `margin link` and the serve loop never create the same document twice.
- The skill makes margin the default way to deliver any document, with rules for where a document goes.

## 0.1.0

- `margin sync` publishes repository markdown to Outline as nested documents, with small verified patches that keep comments attached.
- Edits made in Outline, and changes on both sides, are reported and never overwritten.
- `margin serve` receives signed comment webhooks, runs a catch-up poll, and delivers comments in batches after a quiet period.
- `margin reply`, `resolve`, `thread` and `status`.
- Comments can be routed by path prefix, and the notifier command can take the rendered message as `{text}` and run in a chosen folder.
- The notifier command can also take `{repo}` and `{repo_path}`.
