# Outline for margin

1. Create a Postgres database and role named `outline`.
2. Copy `outline.env.example` to `outline.env` and `dex.yaml.example` to `dex.yaml`, and fill in the secrets.
3. `docker compose up -d`, and put your TLS proxy in front of ports 3100 (Outline) and 5556 (Dex).
4. Sign in as yourself first, so your account becomes the admin. Then sign in as the agent.
5. In each account, open Settings, then API Keys, and create a key. Give margin the agent's key as `api_key_command` and yours as `admin_key_command`.

The agent's account stays a normal member. Your key is used once, to create the webhook.
