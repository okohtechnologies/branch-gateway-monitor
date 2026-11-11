"""Interactive Telegram bot: commands (FR-18) and Acknowledge/Mute buttons (FR-16, FR-17).

Long-polls getUpdates. Only messages from authorised chat IDs are acted on; anything
else is ignored silently (no reply, so the bot does not reveal that it exists).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from .db import Database
from .notifier import TelegramApi, humanize
from .runner import Monitor

log = logging.getLogger(__name__)

EMOJI = {"UP": "🟢", "DEGRADED": "🟠", "DOWN": "🔴", "PENDING": "⚪", "DISABLED": "⚫"}
MAX_MUTE_MINUTES = 7 * 24 * 60

HELP = (
    "Branch Gateway Monitor\n\n"
    "/status - all branches at a glance\n"
    "/branch <name> - details for one branch\n"
    "/mute <name> <duration> - silence alerts, e.g. /mute HQ 30m (m, h or d)\n"
    "/unmute <name> - cancel a mute\n"
    "/incidents - open incidents\n"
    "/help - this message"
)

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.I)
_CALLBACK = re.compile(r"^(ack:(\d+)|mute:(\d+):(\d+)|unmute:(\d+))$")


def parse_duration(text: str) -> int | None:
    """'30m' / '2h' / '1d' -> minutes. None if invalid or outside 1 minute..7 days."""
    m = _DURATION.match(text.strip())
    if not m:
        return None
    minutes = int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2).lower()]
    return minutes if 1 <= minutes <= MAX_MUTE_MINUTES else None


def humanize_minutes(minutes: int) -> str:
    return humanize(minutes * 60)


def local_hhmm(when: datetime) -> str:
    return when.astimezone().strftime("%H:%M")


def _without(rows: list, prefix: str) -> list:
    out = [[b for b in row if not b["callback_data"].startswith(prefix)] for row in rows]
    return [r for r in out if r]


def _swap(rows: list, old_prefix: str, new_button: dict) -> list:
    return [[new_button if b["callback_data"].startswith(old_prefix) else b for b in row] for row in rows]


class Bot:
    def __init__(self, api: TelegramApi, db: Database, monitor: Monitor, allowed_chat_ids: set[int]):
        self.api, self.db, self.monitor = api, db, monitor
        self.allowed = allowed_chat_ids

    # --- polling loop -------------------------------------------------------------

    async def run_forever(self) -> None:
        offset: int | None = None
        backoff = 1.0
        while True:
            payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                payload["offset"] = offset
            ok, updates, err = await self.api.call("getUpdates", payload, retries=0, timeout=40)
            if not ok:
                log.warning("getUpdates failed (%s); retrying in %.0fs", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)
                continue
            backoff = 1.0
            for update in updates or []:
                offset = update["update_id"] + 1
                try:
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("error handling update %s", update.get("update_id"))

    async def handle_update(self, update: dict) -> None:
        if "message" in update:
            msg = update["message"]
            if msg.get("chat", {}).get("id") not in self.allowed:  # FR-18
                log.warning("ignored message from unauthorised chat %s", msg.get("chat", {}).get("id"))
                return
            await self._on_message(msg)
        elif "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq.get("message", {}).get("chat", {}).get("id")
            if chat_id not in self.allowed:
                log.warning("ignored button press from unauthorised chat %s", chat_id)
                return
            await self._on_callback(cq)

    # --- commands -----------------------------------------------------------------------

    async def _say(self, chat_id: int, text: str) -> None:
        await self.api.call("sendMessage", {"chat_id": chat_id, "text": text})

    async def _on_message(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        chat_id = msg["chat"]["id"]
        head, _, args = text.partition(" ")
        cmd = head[1:].split("@")[0].lower()
        args = args.strip()
        who = self._who(msg.get("from", {}))

        handler = {
            "start": self._help, "help": self._help, "status": self._status,
            "incidents": self._incidents,
        }.get(cmd)
        if handler:
            await handler(chat_id)
        elif cmd == "branch":
            await self._branch(chat_id, args)
        elif cmd == "mute":
            await self._mute(chat_id, args, who)
        elif cmd == "unmute":
            await self._unmute(chat_id, args)
        else:
            await self._say(chat_id, "Unknown command. Send /help for the list.")

    async def _help(self, chat_id: int) -> None:
        await self._say(chat_id, HELP)

    def _status_of(self, row) -> str:
        if not row["enabled"]:
            return "DISABLED"
        confirmed = self.monitor.engine.confirmed(row["id"])
        return confirmed.value if confirmed else "PENDING"

    def _latency_text(self, row) -> str:
        """Live reading if we have one, else the last stored check, else 'no data yet'."""
        snap = self.monitor.latest.get(row["id"])
        if snap:
            return self._ms(snap["avg_latency_ms"])
        keys = row.keys()
        if "last_ts" in keys and row["last_ts"] is not None:  # overview rows carry the last check
            return self._ms(row["avg_latency_ms"])
        return "no data yet"

    @staticmethod
    def _ms(v: float | None) -> str:
        return "no reply" if v is None else f"{v:g} ms"

    async def _status(self, chat_id: int) -> None:
        rows = await self.db.overview()
        if not rows:
            await self._say(chat_id, "No branches are configured yet.")
            return
        lines, counts = [], {}
        for r in rows:
            st = self._status_of(r)
            counts[st] = counts.get(st, 0) + 1
            muted = " 🔕" if await self.db.muted_until(r["id"]) else ""
            detail = "disabled" if st == "DISABLED" else self._latency_text(r) if st != "PENDING" else "waiting"
            lines.append(f"{EMOJI[st]} {r['name']} - {detail}{muted}")
        summary = ", ".join(f"{n} {s.lower()}" for s, n in counts.items())
        await self._say(chat_id, f"{summary}\n\n" + "\n".join(lines))

    async def _find(self, chat_id: int, name: str):
        """Resolve a branch by name: exact match, else a unique partial match."""
        if not name:
            await self._say(chat_id, "Please give a branch name.")
            return None
        rows = await self.db.all_branches()
        exact = [r for r in rows if r["name"].lower() == name.lower()]
        matches = exact or [r for r in rows if name.lower() in r["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            await self._say(chat_id, f"No branch named '{name}'. Send /status to see them.")
        else:
            await self._say(chat_id, "Several branches match: " + ", ".join(r["name"] for r in matches))
        return None

    async def _branch(self, chat_id: int, name: str) -> None:
        found = await self._find(chat_id, name)
        if found is None:
            return
        # The overview row also carries the last stored check (used after a restart).
        row = next((r for r in await self.db.overview() if r["id"] == found["id"]), found)
        st = self._status_of(row)
        up = (await self.db.uptime(row["id"]))["24h"]
        last = await self.db.incidents(row["id"], limit=1)
        snap = self.monitor.latest.get(row["id"])
        lines = [
            f"{EMOJI[st]} {row['name']} - {st.lower()}",
            f"IP: {row['host']}" + (f":{row['port']}" if row["probe_type"] == "tcp" else ""),
            f"Latency: {self._latency_text(row)}",
        ]
        if snap:
            lines.append(f"Loss: {snap['loss_pct']:g}%")
        lines.append(f"24h uptime: {'no data' if up is None else f'{up:.2f}%'}")
        if last:
            i = last[0]
            now = datetime.now(timezone.utc)
            span = humanize(((i["ended_at"] or now) - i["started_at"]).total_seconds())
            state = "ongoing" if i["ended_at"] is None else "resolved"
            lines.append(f"Last incident: {i['severity'].lower()} {state}, {span}")
        else:
            lines.append("Last incident: none")
        until = await self.db.muted_until(row["id"])
        if until:
            lines.append(f"🔕 Muted until {local_hhmm(until)}")
        await self._say(chat_id, "\n".join(lines))

    async def _mute(self, chat_id: int, args: str, who: str) -> None:
        name, _, dur = args.rpartition(" ")
        minutes = parse_duration(dur) if name else None
        if minutes is None:
            await self._say(chat_id, "Usage: /mute <name> <duration>, e.g. /mute HQ 30m (1m to 7d).")
            return
        row = await self._find(chat_id, name.strip())
        if row is None:
            return
        until = await self.db.mute_branch(row["id"], minutes, who)
        await self._say(chat_id, f"🔕 {row['name']} muted for {humanize_minutes(minutes)} (until {local_hhmm(until)}). "
                                 "Probing continues; only alerts are paused.")

    async def _unmute(self, chat_id: int, name: str) -> None:
        row = await self._find(chat_id, name)
        if row is None:
            return
        if await self.db.unmute_branch(row["id"]):
            await self._say(chat_id, f"🔔 {row['name']} unmuted.")
        else:
            await self._say(chat_id, f"{row['name']} is not muted.")

    async def _incidents(self, chat_id: int) -> None:
        rows = await self.db.incidents(open_only=True, limit=20)
        if not rows:
            await self._say(chat_id, "✅ No open incidents.")
            return
        now = datetime.now(timezone.utc)
        lines = []
        for i in rows:
            ack = f", ack by {i['acknowledged_by']}" if i["acknowledged_by"] else ""
            lines.append(f"{EMOJI[i['severity']]} {i['branch_name']} - {i['severity'].lower()} for "
                         f"{humanize((now - i['started_at']).total_seconds())}{ack}")
        await self._say(chat_id, f"{len(rows)} open incident(s):\n\n" + "\n".join(lines))

    # --- buttons ---------------------------------------------------------------------------

    @staticmethod
    def _who(user: dict) -> str:
        return user.get("username") and f"@{user['username']}" or user.get("first_name") or "someone"

    async def _toast(self, cq: dict, text: str) -> None:
        await self.api.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text})

    async def _edit(self, cq: dict, text: str | None, rows: list) -> None:
        msg = cq["message"]
        payload = {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                   "reply_markup": {"inline_keyboard": rows}}
        if text is not None:
            payload["text"] = text
            await self.api.call("editMessageText", payload, retries=0)
        else:
            await self.api.call("editMessageReplyMarkup", payload, retries=0)

    async def _on_callback(self, cq: dict) -> None:
        m = _CALLBACK.match(cq.get("data") or "")
        if not m:
            await self._toast(cq, "This button is no longer valid.")
            return
        who = self._who(cq.get("from", {}))
        msg = cq["message"]
        rows = msg.get("reply_markup", {}).get("inline_keyboard", [])

        if m.group(2):  # ack:<incident_id>
            result = await self.db.acknowledge_incident(int(m.group(2)), who)
            if result == "acknowledged":
                await self._toast(cq, "Acknowledged")
                stamp = local_hhmm(datetime.now(timezone.utc))
                await self._edit(cq, f"{msg.get('text', '')}\n\n✅ Acknowledged by {who} at {stamp}",
                                 _without(rows, "ack:"))
            elif result == "already":
                await self._toast(cq, "Someone has already acknowledged this.")
                await self._edit(cq, None, _without(rows, "ack:"))
            else:
                await self._toast(cq, "This incident is already resolved.")
                await self._edit(cq, None, [])
        elif m.group(3):  # mute:<branch_id>:<minutes>
            branch_id, minutes = int(m.group(3)), int(m.group(4))
            if not 1 <= minutes <= MAX_MUTE_MINUTES or await self.db.get_branch(branch_id) is None:
                await self._toast(cq, "This button is no longer valid.")
                return
            await self.db.mute_branch(branch_id, minutes, who)
            await self._toast(cq, f"Muted for {humanize_minutes(minutes)}")
            await self._edit(cq, None, _swap(rows, "mute:", {"text": "🔔 Unmute", "callback_data": f"unmute:{branch_id}"}))
        else:  # unmute:<branch_id>
            branch_id = int(m.group(5))
            await self.db.unmute_branch(branch_id)
            await self._toast(cq, "Unmuted")
            await self._edit(cq, None, _swap(rows, "unmute:", {"text": "🔕 Mute 1h", "callback_data": f"mute:{branch_id}:60"}))
