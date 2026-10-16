---
name: margin
description: Use whenever you write or change documentation, a plan, a spec, a design, a report, notes or any markdown a person is meant to read, and whenever a message says "new comment(s) in Outline". margin publishes repository markdown to Outline, where the person reads it and comments on it, and brings the comments back to you. Covers where a document goes, publishing it, giving the person its link, and answering comments.
---

# margin

Documentation lives as markdown files in the repository that owns the subject. margin publishes
those files to Outline, where the person reads them in a browser or on a phone and comments on a
piece of text. Each repository is one Outline collection, its folders are parent pages, and a
folder's `README.md` is that folder's page. The comments come back to you in batches.

This is the default way to give the person any document. Do not deliver a plan, a spec or a
design only as chat text, an artifact or a file outside a published repository, unless the person
asks for that.

## Where a document goes

The folder structure in the repository is the structure the person sees in Outline, so it has to
stay right.

1. **Pick the repository that owns the subject.** That is the repository whose code or decision it
   is about. When a subject spans several repositories, it goes in the one that owns the decision,
   and the others link to it. When no repository owns it, use the notes collection if the
   configuration has one.
2. **Follow that repository's own conventions.** Read its `CLAUDE.md` and its `README.md` first.
   Put the file next to documents of the same kind (plans with plans, specs with specs). Never
   start a second docs tree beside the one that exists.
3. **Name and title it properly.** The file name is lowercase words joined by dashes, with a date
   prefix when the folder already uses one. The first `# Heading` becomes the page title, so make
   it a real name for the document.
4. **A new folder needs a reason.** When you do create one, give it a `README.md` that says what
   the folder holds, because that file is the folder's page.

## Publishing, and giving the person the link

After you create or change a document, publish it and hand over the link.

```bash
margin link path/to/doc.md [more files...]
```

It publishes those files at once and prints one Markdown link per file. A file in a git
worktree of a published repository works too: margin finds the repository through git, and keeps
publishing the worktree's version (the sync pass does not put the main folder's older text back)
until the main folder has the same text, or the worktree file is gone. **Put every link it
prints in your reply to the person**, so they can open each document you wrote or changed. Use only
links margin printed. Never build an Outline address yourself. If margin prints "not published",
tell the person which file and why, and fix it (move the file into a published repository, or ask
for the repository to be added to the configuration). "not published: ... is conflict" (or
`cannot-patch`, `drift`, `outline-edited`) means the document still has an older text, and `margin
link` exits non-zero: the document is not published until the conflict is settled (below).

To show a picture, save it next to the document (for example in a `shots/` folder beside it) and
refer to it by a relative path, `![What it shows](shots/name.png)`. margin uploads it to the
document and keeps it in sync; a changed picture republishes the document. Never paste an
Outline attachment address into a file.

Do not put a link or a code span inside bold text, as in `**A follow-up, [#581](url).**`. Outline
rewrites it as `**A follow-up,** [**#581**](url)**.**`, so the document can never equal the file and
every update of it fails. Put the link after the bold label: `**A follow-up.** [#581](url)`.

## Answering a batch of comments

1. Read the whole batch first. Several comments are often about one change.
2. For each comment, decide what it asks for. It can be an answer, a change to the document, or both.
3. If it asks for a change, edit the repository file (the path is under the document's title),
   then run `margin link` on it. Never edit the document in Outline directly.
4. Answer every comment in its own thread, even when the answer is only "changed it", so the person
   can see what happened without asking. Use `margin reply <comment-id> "..."`.
5. Run `margin resolve <comment-id>` only when the person's question is fully settled. When you are
   not sure, leave it open.

Use `margin thread <comment-id>` to read the whole thread when a comment is a reply.

## Rules

- Your replies are posted as the agent's own Outline user. Write them as yourself, in plain words.
- Keep edits small. A comment attached to text you rewrite loses its highlight. margin patches only
  the words that changed, so a comment on words you keep stays highlighted, even in a paragraph you
  edited. Change only what the comment is about.
- Never replace a whole document, never delete or move one, and never share one publicly. Sharing
  is the person's decision. Moving a page is done by moving its file.
- If `margin sync` or `margin link` reports `cannot-patch` or `drift`, nobody edited the document:
  margin could not patch it into the file's text. Run `margin accept-file <file>`, which publishes
  the file (it checks that only margin has edited the document), and do not ask the person about it.
- If `margin sync` or `margin link` reports `conflict` or `outline-edited`, run
  `margin accept-file <file>`. It lets the file win only when nobody but margin edited the
  document since the last sync and it has no comments, and otherwise it refuses and names who
  edited it. When it refuses, the person changed the document in Outline: do not force your
  version. Read the document (Outline's MCP `fetch`), copy their edit into the file, and run
  `margin accept-merged <file>`. It publishes only when every line they added or changed is in
  the file and no line they removed is, and otherwise refuses and names the line. When their
  edit and yours cannot both stand, tell them which document it is and ask.
- Say a document is published only when `margin link` printed its link in this turn.
