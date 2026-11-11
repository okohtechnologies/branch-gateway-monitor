# PRD: Branch Gateway Monitor

*Product Requirements Document · Version 0.2 (Draft) · Learning project*

**Changelog v0.1 → v0.2:** decisions from the brainstorm: VPN-only gateways and in-network hosting, PostgreSQL from day one, a vertical-slice build order with a Slice 0 spike, a canary probe and mass-failure alert moved into the MVP, HTTPS requirement relaxed, grouped alerts reduced to the mass-failure summary. Changed text is marked *(v0.2)*.

## 1. Overview

Branch Gateway Monitor is a web dashboard and Telegram bot that continuously checks the IP gateways of a company's branch offices. It detects outages and high latency, notifies the right people instantly, and keeps history so trends can be reviewed.

**Project purpose:** a production-style learning project covering network probing, async Python, REST and WebSocket APIs, a React dashboard, Telegram bot development, and reliable deployment. It may be deployed for real use later, so it is built to production standards where cheap to do so.

## 2. Problem Statement

Companies with several branches depend on each branch's internet gateway. Today, problems are usually discovered when staff complain, which means slow response and no record of how often a branch has been unstable. There is no single view of branch health and no automatic alerting.

## 3. Goals and Non-Goals

### Goals

- Detect a branch gateway outage within 2 minutes.
- Detect sustained high latency or packet loss and alert before users complain.
- Alert via Telegram with actionable buttons (Acknowledge, Mute).
- Show live status and historical latency in a clean dashboard.
- Be reliable: no false-alarm floods, and a warning if the monitor itself dies.
- *(v0.2)* Distinguish "a branch is down" from "the monitor lost its own network".

### Non-Goals (v1)

- Deep device monitoring (SNMP, bandwidth per interface, CPU of routers).
- Automatic remediation (rebooting devices).
- Multi-company SaaS or billing.
- Native mobile app (Telegram covers mobile).
- *(v0.2)* Public internet exposure of the dashboard.

## 4. Users and Personas

| Persona | Needs |
| --- | --- |
| **Network/IT admin** (primary, and the single alert recipient in v1) | Instant alerts, live status, history to diagnose and report on reliability |
| **IT manager** | Summary of uptime per branch, weekly report |
| **Branch manager** (secondary) | Simple way to see if their site is up |

## 5. Constraints and Assumptions *(v0.2, new)*

- **Scale:** 1–10 branches at launch. The design should not preclude 100, but is not optimised for it.
- **Network:** branch gateways are reachable only from inside the company VPN/network. The monitor runs inside that network.
- **Hosting:** the host is not yet chosen (server, VM, or small device). Everything runs in Docker so the host can be decided later.
- **Alert recipients:** one person in v1. Per-branch routing and groups are v2.
- **Egress:** outbound access from the host to Telegram and the heartbeat service is unverified. Slice 0 checks it. If restricted, proxy support is added.
- **ICMP:** whether gateways answer ICMP is unverified. Slice 0 checks it. If not, TCP fallback is required in the MVP rather than optional.

## 6. User Stories

1. As an admin, I want to add, edit, and remove branches so the monitored list stays current.
2. As an admin, I want a Telegram message when a branch goes down so I can act immediately.
3. As an admin, I want a message when latency stays high so I can investigate before an outage.
4. As an admin, I want a recovery message so I know the issue has cleared.
5. As an admin, I want to tap **Acknowledge** on an alert so my team knows it is being handled.
6. As an admin, I want to mute a branch for a set time during maintenance so I am not spammed.
7. As an admin, I want to type `/status` in Telegram to see all branches at a glance.
8. As a manager, I want a dashboard with live status and latency graphs for each branch.
9. As a manager, I want uptime percentages per branch over 24h, 7d, and 30d.
10. As an admin, I want to be warned if the monitoring system itself stops working.
11. *(v0.2)* As an admin, I want one "monitor may be offline" alert, not a flood of per-branch alerts, when all branches fail at once.

## 7. Scope

### MVP (v1)

- Branch management (CRUD)
- ICMP ping probing every 30 seconds, with TCP check as fallback *(mandatory in MVP if Slice 0 shows ICMP is blocked)*
- Status engine with debouncing (UP, DEGRADED, DOWN)
- *(v0.2)* Canary probe and mass-failure ("monitor may be offline") alert
- Telegram alerts with Acknowledge and Mute buttons
- Bot commands: `/status`, `/branch <name>`, `/mute`, `/help`
- Dashboard: status grid, branch detail page with latency chart
- Login (single admin role)
- History storage and uptime calculation
- Dead-man's-switch heartbeat

### v2

- Multiple users and roles (admin, viewer)
- Multiple alert recipients and per-branch routing
- Escalation (re-alert another person if not acknowledged)
- Maintenance windows (scheduled)
- Email and SMS channels
- Weekly summary report
- Branch map view
- Second probe location
- Raw-check retention policy and hourly aggregation job *(deferred from v1)*

### v3

- Lightweight agent at each branch (probes the internet from inside)
- SNMP interface metrics
- Public status page

## 8. Functional Requirements

### 8.1 Branch Management

| ID | Requirement |
| --- | --- |
| FR-1 | The system shall store for each branch: name, gateway IP/hostname, location, latency threshold (ms), packet-loss threshold (%), enabled flag. |
| FR-2 | Admin can create, edit, disable, and delete branches from the dashboard. |
| FR-3 | New branches start being probed within one probe cycle, without restart. |
| FR-4 | Input validation rejects invalid IPs or hostnames and duplicate names. |

### 8.2 Probing

| ID | Requirement |
| --- | --- |
| FR-5 | Each enabled branch is probed every N seconds (default 30, configurable). |
| FR-6 | A probe sends 4 ICMP echo requests with a 2-second timeout and records average latency and packet loss. |
| FR-7 | If ICMP is blocked, a branch may be configured to use a TCP connect check (host and port). |
| FR-8 | Probes run concurrently so one slow branch never delays the others. |
| FR-9 | Every probe result is stored with timestamp, latency, loss, and probe status. |
| FR-32 | *(v0.2)* Each probe cycle also probes a configurable canary target outside the branch network (default: a public DNS resolver such as 1.1.1.1). Canary results are stored but are not a branch. *(v0.3)* The default canary is a TCP connect to 1.1.1.1:443, not ICMP, because many networks block ICMP to public addresses (observed on a home network during Slice 0). ICMP canaries remain configurable. |

### 8.3 Status Engine

| ID | Requirement |
| --- | --- |
| FR-10 | **DOWN:** all packets lost. **DEGRADED:** average latency or loss above the branch's thresholds. **UP:** otherwise. |
| FR-11 | A status change is confirmed only after 3 consecutive matching results (configurable) to prevent flapping. |
| FR-12 | Each confirmed change creates an **incident** record with start time, end time, severity, and acknowledgement info. |
| FR-13 | While a branch remains DOWN or DEGRADED, a reminder is sent every 30 minutes (configurable) until recovery or acknowledgement. |
| FR-33 | *(v0.2)* **Monitor-suspect state:** if the canary fails AND all enabled branches (minimum 2 branches) are failing in the same confirmation window, the engine enters a MONITOR_SUSPECT state. Per-branch DOWN alerts are suppressed and one "monitor may be offline" alert is sent. Incidents are not opened for branches while in this state. |
| FR-34 | *(v0.2)* On leaving MONITOR_SUSPECT, branches that are still failing are re-evaluated normally and alert as usual. A single recovery message is sent for the monitor-suspect alert. |

### 8.4 Notifications (Telegram)

| ID | Requirement |
| --- | --- |
| FR-14 | The system sends a Telegram message on: DOWN, DEGRADED, RECOVERED, reminder, and monitor-suspect. |
| FR-15 | Alert messages include branch name, IP, status, latency, loss, and time. |
| FR-16 | DOWN and DEGRADED alerts include inline buttons: **Acknowledge**, **Mute 1h**. |
| FR-17 | Acknowledge records who acknowledged and when, and updates the original message. |
| FR-18 | Only authorised Telegram chat IDs can use bot commands and buttons. Others are ignored. |
| FR-19 | *(v0.2)* Alerts to a chat are rate-limited. Mass-outage grouping is handled by FR-33. If several, but not all, branches fail together, their alerts are batched into one message. |
| FR-20 | If a notification fails, it is retried with backoff and the failure is logged. |

### 8.5 Telegram Bot Commands

| Command | Behaviour |
| --- | --- |
| `/status` | List all branches with status emoji and latest latency |
| `/branch <name>` | Detail for one branch: status, latency, 24h uptime, last incident |
| `/mute <name> <duration>` | Suppress alerts for that branch (e.g. 30m, 2h) |
| `/unmute <name>` | Cancel a mute |
| `/incidents` | Show currently open incidents |
| `/help` | List commands |

### 8.6 Dashboard

| ID | Requirement |
| --- | --- |
| FR-21 | Overview page shows a card per branch: name, status colour, current latency, 24h uptime. |
| FR-22 | Status updates appear live (within 2 seconds of a result) without page refresh. |
| FR-23 | Branch detail page shows a latency chart (1h, 24h, 7d), packet-loss chart, and incident list. |
| FR-24 | Overview supports filtering by status and searching by name. |
| FR-25 | Dashboard is responsive and usable on a phone. |
| FR-26 | Dark and light themes. |

### 8.7 Auth and Security

| ID | Requirement |
| --- | --- |
| FR-27 | Dashboard and API require login. Passwords are hashed (argon2 or bcrypt). |
| FR-28 | Secrets (bot token, DB password) live in environment variables, never in code or Git. |
| FR-29 | *(v0.2)* The dashboard is served only inside the company network. TLS is optional for internal use and recommended if exposed beyond a single LAN or VPN segment. The dashboard is never exposed to the public internet. |

### 8.8 Self-Monitoring

| ID | Requirement |
| --- | --- |
| FR-30 | The monitor sends a heartbeat to an external dead-man's-switch service (e.g. healthchecks.io) every minute. If it stops, that service alerts the admin. *(v0.3)* The heartbeat is sent only while the monitor is healthy (database reachable and a probe cycle completed recently, the same check `/health` uses), so a hung probe loop also triggers the external alert. The ping URL is a secret and is never logged. |
| FR-31 | A `/health` endpoint reports probe-loop and database status. |

## 9. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| Reliability | Target 99.5% monitor uptime. Auto-restart on crash. State recovered from the database after restart. |
| Performance | *(v0.2)* Comfortably support 10 branches at 30-second intervals. Stretch target: 100 branches on a small host (1 vCPU, 1 GB RAM). |
| Accuracy | False-alarm rate under 1 per branch per month under normal conditions. |
| Latency of alerting | DOWN detected and notified within 2 minutes of failure. |
| Data retention | *(v0.2)* v1 keeps all raw checks. The 30-day raw / 1-year hourly aggregate policy is deferred to v2. |
| Usability | A new admin can add a branch in under 1 minute. |
| Maintainability | Test coverage above 70% on the status engine; documented setup. |

## 10. Alert Logic Specification

**States:** UP → DEGRADED → DOWN (and back). Plus a global MONITOR_SUSPECT overlay *(v0.2)*.

**Example (confirm = 3, interval = 30s):**

- Check 1: no replies, pending DOWN (no alert).
- Check 2: no replies, pending DOWN (no alert).
- Check 3: no replies, DOWN confirmed, alert sent, incident opened.
- Later: 3 consecutive good checks, RECOVERED alert, incident closed with duration.

**Mass-failure example (v0.2):** all branches and the canary fail for 3 consecutive cycles. The engine enters MONITOR_SUSPECT and sends one "monitor may be offline" alert, with no per-branch DOWN alerts. If the canary succeeds but all branches fail, the cause is likely the VPN or the internal network, so the engine does NOT suppress. It alerts per FR-33's conditions only.

**Muting:** probes and history continue while muted, only notifications are suppressed. Mutes expire automatically.

## 11. Data Model (high level)

| Table | Key fields |
| --- | --- |
| `branches` | id, name, host, location, probe\_type, port, latency\_warn\_ms, loss\_warn\_pct, enabled |
| `checks` | id, branch\_id (null for canary), timestamp, avg\_latency\_ms, loss\_pct, status |
| `incidents` | id, branch\_id, severity, started\_at, ended\_at, acknowledged\_by, acknowledged\_at |
| `notifications` | id, incident\_id, channel, sent\_at, success, error |
| `users` | id, email, password\_hash, role |
| `telegram_chats` | id, chat\_id, label, authorised |
| `mutes` | id, branch\_id, until, created\_by |

## 12. Proposed Architecture and Tech Stack

| Layer | Choice | Why |
| --- | --- | --- |
| Probe and API | Python 3.12, FastAPI, asyncio, icmplib | Async concurrency, great docs |
| Telegram bot | python-telegram-bot (or aiogram) | Async, inline buttons, mature |
| Database | *(v0.2)* PostgreSQL in Docker from day one (TimescaleDB later if needed) | No migration later, time-series friendly |
| Realtime | WebSockets via FastAPI | Live dashboard |
| Frontend | React, TypeScript, Vite, Tailwind, Recharts or ECharts | Modern, interactive |
| Deployment | *(v0.2)* Docker Compose on a host inside the VPN/company network (host TBD) | Reproducible, host-agnostic, can reach VPN-only gateways |
| Monitoring the monitor | healthchecks.io plus canary probe | Dead-man's-switch plus network-fault detection |
| Testing | pytest, Vitest | Reliability |

**Flow:** Scheduler → probe workers → status engine → database → (alert dispatcher → Telegram) and (WebSocket → dashboard).

**Note:** ICMP in Docker needs `NET_RAW` capability (or privileged/unprivileged-ICMP sysctl). Verify in Slice 0.

## 13. Key Screens

1. **Login**
2. **Overview:** grid of branch cards, summary bar (X up, Y degraded, Z down), search and filters.
3. **Branch detail:** live status, latency and loss charts, incident history, edit button.
4. **Branches:** table with add, edit, disable, delete.
5. **Incidents:** list with filters and acknowledgement state.
6. **Settings:** thresholds, intervals, Telegram chat IDs, canary target, account.

## 14. Milestones *(v0.2: vertical-slice order)*

| Stage | Deliverable |
| --- | --- |
| 0 | **Spike:** from the candidate host, ping one real gateway (ICMP, and TCP if blocked), send one Telegram message, reach the heartbeat service, confirm ICMP works inside Docker. Record results; settle the open items in section 5. |
| 1 | **Vertical slice:** async probe loop, status engine with debouncing and unit tests, PostgreSQL storage, Telegram DOWN and RECOVERED alerts, for branches in a config file. Includes the canary probe and monitor-suspect logic. |
| 2 | FastAPI endpoints (branches CRUD, history, incidents); branches move from config file to the database |
| 3 | React dashboard with live updates |
| 4 | Interactive bot: commands, Acknowledge and Mute buttons |
| 5 | Auth, Docker Compose packaging, dead-man's-switch, documentation |

## 15. Success Metrics

- Outage detection to alert time: under 2 minutes in 95% of cases.
- False alerts: under 1 per branch per month.
- *(v0.2)* A simulated monitor network outage produces exactly one alert, not one per branch.
- Alert acknowledged within 10 minutes on average.
- Dashboard shows data less than 5 seconds old.
- All MVP user stories demonstrable end to end.

## 16. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| ICMP blocked at some gateways | TCP/HTTP probe fallback; verified in Slice 0 |
| *(v0.2)* Gateways are VPN-only, so the monitor depends on the VPN being up | Host inside the network; canary distinguishes monitor/uplink faults; a VPN outage appears as all-branches-down and is alerted as such |
| Monitor's own network fails, causing false "all down" | Canary probe and MONITOR_SUSPECT state (FR-32 to FR-34); dead-man's-switch; second probe location in v2 |
| *(v0.2)* Host cannot reach Telegram or heartbeat service (restricted egress) | Verified in Slice 0; add proxy support if needed |
| *(v0.2)* Host not yet chosen | Docker Compose packaging keeps the host swappable |
| Alert fatigue | Debounce, mute, batched alerts, sensible thresholds |
| Telegram outage or rate limit | Retry with backoff; add email as second channel in v2 |
| Bot token leaked | Env vars, rotation procedure, authorised chat IDs only |
| Scope creep | Strict MVP list; v2 and v3 only after MVP is complete |

## 17. Open Questions

Resolved in the brainstorm: scale (1–10), reachability (VPN-only), recipients (one person), dashboard exposure (internal only), build order (vertical slice), database (PostgreSQL).

Still open, to be settled in Slice 0 or later:

1. Do gateways respond to ICMP, and if not, which TCP port is open?
2. Which host will run the monitor, and does it have outbound access to Telegram and the heartbeat service?
3. Is there a working-hours versus after-hours alert policy?
4. Any existing tool (Uptime Kuma, Zabbix) the company expects this to replace?
5. *(v0.2)* What counts as "all branches failing" when some branches are disabled or muted? The draft rule counts enabled branches only. Muted branches still count, since probing continues.

## 18. Glossary

- **ICMP / ping:** a simple network test measuring reachability and round-trip time.
- **Latency:** round-trip time in milliseconds.
- **Packet loss:** percentage of test packets that get no reply.
- **Debouncing:** requiring several consecutive results before changing status.
- **Incident:** a continuous period of DOWN or DEGRADED status for a branch.
- **Dead-man's-switch:** an external service that alerts you when your system stops sending heartbeats.
- **Canary probe:** *(v0.2)* a probe to a known-good external target, used to tell a monitor-side network fault from a branch fault.
- **MONITOR_SUSPECT:** *(v0.2)* a global state where the monitor believes its own connectivity is the problem, so per-branch alerts are suppressed.
