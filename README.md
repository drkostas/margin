# margin

margin publishes the markdown files of your repositories to a self-hosted [Outline](https://github.com/outline/outline) wiki, and brings every comment you leave there back to an AI agent such as Claude Code. You read your docs in a browser or on your phone, select a sentence and comment on it, and the agent answers in the thread and changes the file.

Each repository becomes one Outline collection. Folders become parent documents, and a folder's `README.md` is that folder's page.

## What it does

- **Publishes changes as small patches.** A comment in Outline is attached to a piece of text. Replacing a whole document through Outline's API removes every one of those attachments, so margin never does that. It works out the smallest changes, applies them one at a time, and checks each result against what it expected.
- **Never settles a disagreement by "newest wins".** margin keeps both sides' text from the last sync. If only the file changed, it patches Outline. If someone edited the document in Outline, or both changed, it leaves both alone and reports the document.
- **Collects comments two ways.** Outline sends a signed webhook for each new comment. A catch-up poll every few minutes finds anything the webhook missed, for example while the machine was asleep. A comment that arrives both ways is recorded once.
- **Sends comments in batches.** A burst of comments becomes one message. A batch is delivered after a quiet period (3 minutes by default), never later than a cap after its first comment (15 minutes), and at once when a comment mentions the agent's user.
- **Keeps the agent out of its own inbox.** Comments written by the agent's user are never recorded, so its replies cannot wake it.

## Install

```bash
pip install margin
```

margin needs Python 3.11 or newer and has no dependencies outside the standard library. You also need an Outline server that you can reach, with two users, yourself and the agent. [`examples/outline`](examples/outline) has a Docker Compose setup for Outline, Redis and a small sign-in provider.

## Configure

margin reads `~/.config/margin/config.toml`. Secrets are never written in it. Each secret is the output of a command, for example a password manager or the macOS keychain.

```toml
[outline]
url = "https://docs.example.com"
api_key_command = ["pass", "outline/agent-api-key"]      # the agent's key, a normal member
admin_key_command = ["pass", "outline/admin-api-key"]    # used only to create the webhook

[intake]
listen = "127.0.0.1:8771"
webhook_url = "http://host.docker.internal:8771/outline" # the address Outline posts to
secret_command = ["pass", "outline/webhook-secret"]
poll_seconds = 600

[batch]
quiet_seconds = 180
cap_seconds = 900

[notify]
command = ["my-notifier", "{route}"]     # receives each batch as JSON on stdin

[[repo]]
name = "my-project"                      # the Outline collection
path = "~/code/my-project"
exclude = ["vendor/**"]
route = "my-project"                     # passed to the notifier as {route}
```

When `[notify]` has no command, margin prints each batch.

Outline refuses to send webhooks to private addresses. If margin runs on the same machine as an Outline container, add the container's host gateway address to Outline's `ALLOWED_PRIVATE_IP_ADDRESSES` setting.

## Use

```bash
margin sync                  # publish what changed, in every configured repo
margin sync my-project --full
margin serve                 # webhook receiver, catch-up poll, batching and a sync every minute
margin status                # documents that need attention, comments waiting
margin reply <comment-id> "Changed to five minutes, see the Lifetime section."
margin resolve <comment-id>
margin thread <comment-id>
```

A batch looks like this.

```text
2 new comment(s) in Outline.

### Cache design
my-project/docs/cache.md  https://docs.example.com/doc/cache-design-pbS4AcgGK0
- [393bdced-...] Alex on "least recently used first":
  Please also say what happens when memory is full.
- [819aea0e-...] Alex on "The cache holds entries":
  Is ten minutes measured from write or from last read?
```

The notifier also receives every comment as structured JSON, with the document's title, link and repository path.

## Limits

- A comment attached to text that the agent then rewrites loses its attachment. The thread stays, with no highlighted text. margin keeps its patches small so that this happens only to text that really changed.
- Outline re-parses a whole list or quote when a patch has to cross into the next block. Comments inside that block lose their attachment the same way. New content at the end of a document is appended and touches nothing.
- Edits made in Outline are reported, not copied back to the file. That direction is planned.

## Licence

MIT
