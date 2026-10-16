# Session memory

## Cloudflare tunnel

- Started `cloudflared tunnel --url http://localhost:8000`.
- Cloudflare created a Quick Tunnel at <https://incident-chess-adjacent-boating.trycloudflare.com>.
- Cloudflare's connectivity pre-checks passed, and the tunnel registered a connection.
- Checked the local origin at `http://localhost:8000`; it responded with HTTP 404 at `/`. The service was reachable, but that route was not found.
- The tunnel process was left running in the background. Quick Tunnel URLs and the process are temporary; they may stop being available when the process exits.