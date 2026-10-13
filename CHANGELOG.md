# Changelog

## Unreleased

- A changed paragraph is patched word by word: each run of changed words is one patch with just enough words around it to be unique, so a comment anchored on words the edit kept keeps its highlight. Checked against Outline's own patch code, where a whole-paragraph patch dropped the comment mark and the word-level one kept it.
- A local image a file shows (`![alt](path/to/picture.png)`, png, jpg, gif, webp or svg) is uploaded as an attachment of its document and the published text points at it, while the file keeps its relative path. Each picture is uploaded once per document and content, and a changed picture republishes the document even when the markdown did not change.
- `margin link` and `margin accept-file` take a file in a git worktree of a configured repository. The worktree's version stays published, and the sync pass does not undo it, until the main folder has the same text or the worktree file is gone.
- `margin link` prints a link only for a document that now says what the file says. A conflict, a document margin could not patch, or one edited in Outline prints "not published: <status>" with the older document's link, and the command exits non-zero.
- `margin accept-merged FILE...` publishes a file that carries a person's Outline edit. It checks that every line they added or changed since the last sync is in the file and no line they removed is, names the line when one is not, and then patches the file in so their comments keep their anchors.
- `margin accept-file FILE...` lets the file win a conflict when nobody but margin edited the document since the last sync and it has no comments, and otherwise refuses and names who edited it.
- A hunk Outline will not take (an empty paragraph it keeps) no longer stops the other hunks of a pass. A document whose words match the file and whose only difference is blank lines is in sync.
- The skill says not to put a link or a code span inside bold text, which Outline rewrites so the document can never match the file.

## 0.2.0

- Titles longer than Outline accepts (100 characters) are cut at a word boundary, and the serve log gives the reason a document failed.
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
