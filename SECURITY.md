# Security

Please report a security problem privately through GitHub's "Report a vulnerability" button on this repository, not in a public issue.

Some things to know before you use it.

- The webhook receiver checks Outline's signature on every request when a secret is configured. Configure one. Without it, anyone who can reach the port can add comments to the queue.
- Bind the receiver to the loopback, as in the example configuration.
- Comments are passed to your agent as text. An agent that can run commands acts on what people write in comments, so only give comment access to people you trust.
- margin stores comment text and document text in a local SQLite file. Keep it on an encrypted disk if your documents are sensitive.
