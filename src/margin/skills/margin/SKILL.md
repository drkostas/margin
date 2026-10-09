---
name: margin
description: Use when a message says "new comment(s) in Outline", when someone asks you to answer or act on comments left on a published markdown document, or when you change a markdown file that margin publishes and need it to appear in Outline. Covers replying in the thread, changing the file, syncing, and resolving.
---

# margin

margin publishes repository markdown to an Outline wiki. A person reads it there and comments on a
piece of text. The comments reach you in one batch, grouped by document, each with its id, the
quoted text it is attached to, and the person's words.

## Answering a batch

1. Read the whole batch first. Several comments are often about one change.
2. For each comment, decide what it asks for. It can be an answer, a change to the document, or both.
3. If it asks for a change, edit the repository file (the path is under the document's title), then
   run `margin sync <repo>`. Never edit the document in Outline directly.
4. Answer every comment in its own thread, even when the answer is only "changed it", so the person
   can see what happened without asking. Use `margin reply <comment-id> "..."`.
5. Run `margin resolve <comment-id>` only when the person's question is fully settled. When you are
   not sure, leave it open.

Use `margin thread <comment-id>` to read the whole thread when a comment is a reply.

## Rules

- Your replies are posted as the agent's own Outline user. Write them as yourself, in plain words.
- Keep edits small. A comment attached to text you rewrite loses its highlight. Change only what the
  comment is about.
- Never replace a whole document and never share a document publicly. Sharing is the person's
  decision.
- If `margin sync` reports `conflict` or `outline-edited`, the person changed the document in
  Outline. Do not try to force your version. Tell them which document it is and ask.
