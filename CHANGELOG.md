# Changelog

## Unreleased

- A change to a table is patched as the whole table, with the blank line on each side. Outline took no patch that began inside a table: rows added at its end came back as a paragraph after it, one row lost, and the document stayed at `cannot-patch`. The spaces Outline writes before an inline picture (`[  ![`, three between two pictures) count as its own spelling, like the escapes and line breaks before. The last six documents stuck at `cannot-patch` converged in a replay against Outline.
- A rewritten document with bold text reaches Outline instead of stopping at `cannot-patch`. Outline writes some text one way when a body is saved whole and another way through a patch (two literal asterisks as `\**` or `\*\*`, a paragraph's line ends as plain newlines or as hard breaks); those count as the same text now, and lines alike keep Outline's own spelling, so margin no longer patches them over and over. A word patch never holds one half of `**`, `__`, `~~` or a code span (Outline escaped the lone half, and the bold turned into literal asterisks); such a change patches its whole line. A hunk planned after one that was set aside no longer ends the pass with "find text not present". Fifteen stuck documents converged in a replay against Outline.
- `margin link` says which kind a failure is: `cannot-patch` and `drift` are margin's own (nobody edited the document, and `margin accept-file` publishes the file), `conflict` and `outline-edited` may be a person's edit.
- `margin fold FILE` folds a markdown file into five levels of detail, from every word down to a map of its sections, the idea of TextFold. Claude Code is asked once per section, with no tools, for each paragraph's key sentences (by number, so they stay the author's own words), its point, a further detail and a topic. Every line it writes is checked by code (numbers, names and code words must be in the sentences it rests on, no added or dropped negation, the paragraph's own words, lengths) and then by a second, stricter call that must name the sentences stating it. A line that fails is asked again once, and then the paragraph keeps its own sentences, marked as the author's. `--out` writes the fold as JSON, `--html` a page to read it, and results are cached by each section's text.
- A version of a file that Outline would not take is not sent again until the file changes (or `--full`). Every patch is an update to the document, and a failed one was retried on every pass, thousands of times a day on the same few documents. It stays reported as `cannot-patch`.
- A Python environment's `site-packages` and a repository cloned inside another repository are skipped: they hold other people's documentation, not the project's. A folder of projects that is not a repository keeps each project.
- A comment on a picture names the picture ("the picture Bedroom TV", from its caption) instead of reading as a comment on the whole document. Outline gives such a comment no anchor text, so margin reads the document's structure (API version 3) to find the picture holding it.
- `margin accept-merged --undo TEXT` names a part of the person's Outline edit they agreed to undo, so the file's version wins there while every other line of their edit must still be in the file.
- A document that no `routes` entry names now sends its comments to whoever last ran `margin link` on it, read from the optional `[notify] author_command`, and to the repo's `route` only when nobody is recorded.
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
