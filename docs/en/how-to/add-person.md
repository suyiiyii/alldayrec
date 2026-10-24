---
type: how-to
---

# How to add another person

One server can serve several people. Each person is one entry (one tenant) in `tenants.json`, with their own token, database and recordings directory. Tenants can't see each other's data.

## Docker

1. Add an entry to the array in `data/tenants.json`. Give it a new name for `id` and the directories, and a newly generated token (`openssl rand -hex 16`):

   ```json
   [
     {"id": "main", "token": "<your existing token>", "data": "/data/main", "recordings": "/data/main/recordings"},
     {"id": "alice", "token": "<new token>", "data": "/data/alice", "recordings": "/data/alice/recordings"}
   ]
   ```

   `data` and `recordings` are paths inside the container. `/data` is `./data` on the host.

2. Restart the server:

   ```bash
   docker compose restart
   ```

   The `tenants:` line in the log lists every tenant, for example `tenants: main, alice`.

3. Give the new token to that person. They open the web UI at `http(s)://<your-server>/?token=<new token>`, then follow [Install and connect the phone app](./install-app.md) to connect their own phone.

## Instances deployed with deploy.sh

Run `server/tenants.sh add <id>`. It adds a tenant to the last instance and creates its token and directories. The details are in the comments at the top of the script.

## Notes

- The server reads `tenants.json` only at startup. Restart it after a change.
- No two tenants may share an `id`, `token`, `data` or `recordings`; otherwise the server refuses to start.
- `tenants.json` must have mode 600 (readable only by you); otherwise the server refuses to start.
- The server skips authentication only when there is a single tenant and its token is empty. Once there is a second person, everyone needs a token.
- The first tenant's data directory also holds the APK the server offers phones for download (`live-record.apk`) and the recorders' firmware packages.
