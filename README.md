# Branch Gateway Monitor

Watches the internet gateways of your branch offices. It detects outages and high latency,
alerts you on Telegram with **Acknowledge** and **Mute** buttons, keeps history, and shows
live status in a web dashboard. It also pings an external dead-man's-switch so you are told
if the monitor itself dies.

```
probes (ICMP/TCP) -> status engine (debounce) -> PostgreSQL
                                   |-> Telegram alerts, reminders, bot commands
                                   '-> WebSocket -> dashboard
```

The product spec is in [PRD Branch Gateway Monitor.md](PRD%20Branch%20Gateway%20Monitor.md).

## Before you start

The monitor must run on a machine that can **reach the branch gateways** (inside the company
network or VPN) and **reach the internet** (Telegram, the heartbeat service).
Run `python spike/spike.py --gateway <ip>` on the intended host first. It checks ICMP to a
gateway, Telegram reachability and, optionally, a test message and a heartbeat ping.

## Quick start (Docker Compose)

Requires Docker with Compose v2.

1. **Create a Telegram bot.** In Telegram, message `@BotFather`, send `/newbot`, and copy the token.
2. **Configure.** Copy `.env.example` to `.env` and fill in `POSTGRES_PASSWORD`, `SECRET_KEY`
   and `TELEGRAM_BOT_TOKEN`.
3. **Find your chat ID.** Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id":...}`.
   Put it in `.env` as `TELEGRAM_CHAT_ID`.
4. **Start.** `docker compose up -d --build`
5. **Create your login.**
   `docker compose exec app python scripts/create_admin.py you@example.com`
   (it prompts for a password of at least 10 characters).
6. **Open** <http://127.0.0.1:8000>, sign in, and add your first branch.

Only the chat ID in `TELEGRAM_CHAT_ID` can use the bot. Messages from any other chat are
ignored without a reply.

### Reaching the dashboard from other machines

The port is published on `127.0.0.1` only. To allow your company network, edit the `ports`
line in `docker-compose.yml` to the host's LAN address, for example `"192.168.1.20:8000:8000"`.
The dashboard has no TLS of its own. **Do not expose it to the public internet.** If you put it
behind an HTTPS reverse proxy, set `COOKIE_SECURE=true`.

## Dead-man's-switch heartbeat

1. Create a free check at [healthchecks.io](https://healthchecks.io): period 1 minute, grace
   time about 5 minutes, and an email or Telegram integration.
2. Put its ping URL in `.env` as `HEARTBEAT_URL`. Treat the URL as a secret.
3. Restart: `docker compose up -d`.

The monitor pings only while it is healthy (database reachable and a probe cycle completed
recently). If it hangs, crashes or loses its network, the pings stop and healthchecks.io
alerts you. Test it once by stopping the app: `docker compose stop app`.

## Using it

**Dashboard:** overview of all branches with live status, a detail page per branch (latency and
loss charts for 1h/24h/7d, uptime, incidents), branch management, and an incident list.

**Telegram:**

| Command | What it does |
| --- | --- |
| `/status` | all branches with status and latest latency |
| `/branch <name>` | status, latency, loss, 24h uptime, last incident |
| `/mute <name> <duration>` | pause alerts, e.g. `/mute HQ 30m` (`m`, `h`, `d`; max 7 days) |
| `/unmute <name>` | cancel a mute |
| `/incidents` | open incidents |
| `/help` | list commands |

Alerts for DOWN and DEGRADED carry **Acknowledge** and **Mute 1h** buttons. While an incident is
open, unacknowledged and unmuted, you get a reminder every 30 minutes.

**How status is decided:**

- **DOWN**: every packet lost. **DEGRADED**: latency or loss above the branch's thresholds.
- A change is confirmed only after 3 consecutive matching results (about 90 seconds), so one
  dropped packet never raises an alert.
- If the **canary** (default TCP to 1.1.1.1:443) and **every** branch fail together, the monitor
  assumes its own network is the problem. It sends one "monitor may be offline" message instead
  of an alert per branch. This needs at least 2 branches.

## Configuration

All settings are environment variables, usually in `.env`. See `.env.example` for the full list.
The important ones: `SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HEARTBEAT_URL`,
`PROBE_INTERVAL_S` (default 30), `CONFIRM` (default 3), `REMINDER_MINUTES` (default 30, 0 = off),
`CANARY_HOST`/`CANARY_PORT`/`CANARY_TYPE`.

Secrets live only in `.env`, which is gitignored. Never commit it or paste its contents into chat
or tickets. To rotate the bot token, revoke it with `@BotFather`, update `.env`, and run
`docker compose up -d`.

## Operations

- **Logs:** `docker compose logs -f app`
- **Update:** `git pull && docker compose up -d --build`
- **Backup the database:**
  `docker compose exec db pg_dump -U ipmon ipmon > backup.sql`
- **Restore:**
  `docker compose exec -T db psql -U ipmon ipmon < backup.sql`
- **Health:** `curl http://127.0.0.1:8000/health` returns 200 when healthy, 503 otherwise.
- **Reset a dashboard password:** run the `create_admin.py` command again with the same email.

Data is kept in the `pgdata` Docker volume. `docker compose down` keeps it;
`docker compose down -v` deletes it.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Every branch shows DOWN | Host not on the VPN/network, or ICMP blocked. Try a TCP probe (branch type "TCP connect" and an open port). |
| Canary alone fails, branches fine | ICMP to the internet is often blocked; keep the canary on TCP (default). |
| App container fails with `Name or service not known` | `POSTGRES_PASSWORD` contains URL characters such as `@ / : # %`. Use letters and digits only (see `.env.example`). If the database volume already exists with the old password, recreate it: `docker compose down -v` (this deletes the database). |
| ICMP fails only in Docker | The container needs `NET_RAW` and the `ping_group_range` sysctl (both set in `docker-compose.yml`). |
| No Telegram messages | Check `docker compose logs app` for `telegram` errors; confirm the token and chat ID, and that you messaged the bot first. |
| Bot silent, log shows a conflict | Two copies of the app are polling the same bot. Run only one. |
| Login says too many attempts | 5 failures lock a client out for 5 minutes. Restart the app to clear it. |
| App refuses to start, mentions `HEARTBEAT_URL` | The URL is malformed; it must be a full `https://...` URL. |

## Run it day to day on Windows (without Docker)

Prerequisites, once: the PostgreSQL service is running, `.env` is filled in (see `.env.example`),
the virtual environment exists, and `npm run build` has been run in `frontend` (the server shows
the dashboard from `frontend/dist`).

**Start the server** (monitoring, Telegram bot, heartbeat and dashboard all run in this one process):

```powershell
& "c:\dev\IP monitor\.venv\Scripts\python.exe" -m ipmon
```

Wait for `Application startup complete`, then open <http://127.0.0.1:8000> and sign in.
It works from any folder. Keep the window open: closing it, or putting the PC to sleep, stops
monitoring. Stop it with **Ctrl+C**.

Rules of thumb:

- Run **one** copy only. A second copy fights the first over the Telegram bot.
- The PC running it must be on the network or VPN that can reach the branch gateways. With the
  VPN off, branches show DOWN.
- After changing `.env` or any code, stop the server (Ctrl+C) and start it again.
- After changing the frontend, run `npm run build` in `frontend`, then reload the browser.
- Check it is healthy: `curl http://127.0.0.1:8000/health` should answer `"status":"ok"`.

For 24/7 monitoring, run it on an always-on machine with Docker (see Quick start above).

## Development

Requires Python 3.12+, Node 22+ and a local PostgreSQL.

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"      # Windows; use .venv/bin/pip on Linux/macOS
# put DATABASE_URL, SECRET_KEY, TELEGRAM_* in .env
.venv\Scripts\python -m pytest             # backend tests (uses throwaway DB schemas)
.venv\Scripts\python -m ipmon              # API + monitor + bot on http://127.0.0.1:8000

cd frontend
npm install
npm test                                   # frontend unit tests
npm run dev                                # hot-reload UI on http://localhost:5173 (proxies to :8000)
npm run build                              # production build, served by the API from frontend/dist
```

Layout: `src/ipmon/` holds `engine.py` (status rules, no I/O), `probe.py`, `runner.py` (the
loop), `notifier.py`, `bot.py`, `heartbeat.py`, `api.py` and `db.py`.
