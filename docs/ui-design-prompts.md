# UI design prompts: Branch Gateway Monitor

How to use: paste the **Global brief** into your design tool first, then paste one **screen
prompt** at a time. Ask for desktop (1440px) and mobile (375px) of each screen. Every screen
prompt lists the states to design; ask for all of them, not just the happy path.

Screens: 1 App shell · 2 Login · 3 Overview · 4 Branch detail · 5 Branches (manage) ·
6 Incidents · 7 Settings (not built yet) · 8 Telegram alert messages (bonus).

---

## Global brief (paste first)

```
I am redesigning the web dashboard for "Branch Gateway Monitor", an internal tool that watches
the internet gateways of a company's branch offices. A backend pings each branch gateway every
30 seconds, decides whether it is UP, DEGRADED (slow or lossy) or DOWN, and alerts the IT admin
on Telegram. The dashboard shows live status, history and incidents.

USERS
- Primary: one network/IT admin who checks the dashboard when a Telegram alert arrives, often
  on a phone, often stressed, and wants the answer to "what is broken, since when, and is it
  getting better?" within two seconds.
- Secondary: an IT manager who glances at uptime percentages; a branch manager who only wants
  to know "is my site up?".
- Scale: 1 to 100 branches. Design must work for 3 branches (cards feel roomy, not empty) and
  for 100 (scannable, filterable, no endless scroll of identical big cards).

PRODUCT PERSONALITY
Calm, precise, trustworthy, like an instrument panel, not a marketing site. When everything is
fine the UI should be quiet and mostly neutral. When something is wrong it must be unmistakable
and the broken thing must rise to the top. Never decorative; every pixel earns its place. No
illustrations, no gradients behind data, no emoji in the UI chrome.

STATUS MODEL (use exactly these)
- UP (healthy), DEGRADED (latency or packet loss above that branch's threshold), DOWN (all
  packets lost), PENDING (new branch, status not yet confirmed, about 90 s), DISABLED (admin
  turned monitoring off).
- Status is NEVER conveyed by colour alone: always colour + text label + a distinct shape or
  icon. Down may subtly pulse (respect prefers-reduced-motion).
- Status colours need 4.5:1 contrast for text on their background in BOTH themes.

THEMES
Dark is the default (operations rooms, night shifts). Light theme must be equally polished.
Design both. Current tokens (keep, refine, or replace, but keep them as named tokens):
  dark:  bg #0d1117, surface #151b23, surface-2 #1c242e, border #2a3340, text #e6eaf0,
         muted #93a0b1, accent #6aa5ff, up #3fb950, degraded #e3a008, down #f85149
  light: bg #f4f5f7, surface #ffffff, surface-2 #eef0f3, border #dde1e7, text #14181f,
         muted #566070, accent #2459d6, up #15803d, degraded #a15c07, down #b91c1c
Typography: a clean sans for UI; tabular (monospaced-digit) numerals for every latency, loss
and uptime figure so values do not jitter as they update; monospace for IP addresses and
hostnames.

LIVE DATA
Values update every 30 s over a WebSocket without page reload. Design how a value changes
(subtle highlight, no layout shift), no "Live" badge (health is silent) but an "Offline, reconnecting" warning
that appears only when the connection drops, and "Updated 12s ago" freshness text. Stale data must look stale.

RESPONSIVE + ACCESSIBILITY
- Design mobile first: 375px phone, 768px tablet, 1440px desktop. Touch targets at least 44px.
- Keyboard reachable everywhere with a visible focus ring; dialogs trap focus and close on Esc.
- WCAG AA contrast, real text labels on every control, no information in tooltips only.

IMPLEMENTATION CONSTRAINTS
It will be built with React, TypeScript and Tailwind CSS, with Recharts for charts. Use
standard components (cards, tables, dialogs, form fields, chips, tabs). Avoid anything that
needs a custom canvas engine. Provide components and states as a small system, not one-off art.

SAMPLE DATA (use this for every mock; do not use lorem ipsum)
Branches:
  Head Office      10.20.0.1    UP        24 ms    0% loss   uptime 24h 100%     7d 99.98%  30d 99.95%
  North Depot      10.20.1.1    DEGRADED  312 ms   8% loss   uptime 24h 97.20%   incident: degraded for 42m
  Riverside Branch 10.20.2.1    DOWN      no reply 100%      uptime 24h 91.60%   incident: down for 14m, not acknowledged
  Airport Kiosk    10.20.3.1    UP        61 ms    0% loss   uptime 24h 99.90%
  Warehouse 2      10.20.4.1    UP        38 ms    1% loss   uptime 24h 99.51%   probe: TCP port 443
  Lab Annex        10.20.5.1    PENDING   (new, waiting for first confirmed result)
  Old Store        10.20.6.1    DISABLED
Incident example: Riverside Branch, severity DOWN, started 14:02 today, still open, not
acknowledged; earlier incidents resolved after 4m, 11m, 2h 5m; one acknowledged by "@alex".
Deliver every screen in dark and light, desktop and mobile.
```

---

## 1. App shell and navigation

```
Design the application shell that wraps every screen after login.

CONTENT
- Product name "Branch Monitor" (small wordmark, no logo art).
- Primary navigation: Overview, Branches, Incidents, Settings.
- Right side: an offline warning that only appears when the live connection drops (dot and text; nothing while healthy), a
  light/dark theme toggle, and an account menu showing the signed-in email with "Sign out".
- A global status strip that appears ABOVE the page content only when something needs
  attention: "1 down · 1 degraded" with a link to the affected branches, always visible while
  scrolling the Overview. Hidden when all is well.
- A banner slot for the special state "Monitor may be offline": every branch and the external
  canary are unreachable at once, so the monitor's own network is the likely cause and
  per-branch alerts are paused. This must be visually different from a normal outage
  (warning, not an alarm) and explain what to do.

BEHAVIOUR
- Desktop: top bar with horizontal nav. Mobile: bottom tab bar with 4 icons plus labels (thumb
  reach), compact top bar with the live dot and a menu for theme and sign out.
- Active route clearly indicated, not by colour alone.

STATES TO DESIGN
Normal; one or more incidents (status strip visible); monitor-suspect banner; disconnected
live feed; light and dark; desktop and mobile.
```

---

## 2. Login

```
Design the sign-in screen for the dashboard.

GOAL
A single admin signs in with email and password. It should feel secure and calm, not
decorative. Nothing on this page should reveal anything about the monitored branches.

CONTENT
- Product name, one line of context ("Sign in to view branch status"), email field, password
  field with show/hide toggle, a primary "Sign in" button.
- No "forgot password" (admin resets it on the server) and no sign-up link. Do not add social
  login.

STATES TO DESIGN
- Default; focused field; submitting (button shows progress, fields disabled).
- Wrong credentials: one generic message ("Invalid email or password"), never saying which
  field was wrong; password field cleared and refocused.
- Locked out: "Too many failed attempts. Try again in 5 minutes." with a visible countdown.
- Server unreachable: "Cannot reach the server" with a Retry action.
- Mobile: the form is vertically centred, keyboard-open layout still shows the button.

ACCESSIBILITY
Labels are visible (not placeholder-only), errors are announced to screen readers and tied to
the fields, browsers' password managers work (proper autocomplete attributes), Enter submits.
```

---

## 3. Overview (the main screen)

```
Design the Overview screen, the page the admin sees most. Its job: show instantly which
branches are healthy and surface anything that is not.

LAYOUT (desktop)
1. Summary bar: four figures, Up / Degraded / Down / Total, each with its status colour and
   label. Tapping a figure filters the list to that status.
2. Controls row: search box (filters by branch name as you type), status filter chips
   (All, Down, Degraded, Up, Pending, Disabled) with counts, a view toggle (cards or compact
   table), and freshness text "Updated 12s ago".
3. The branch list, sorted automatically: DOWN first, then DEGRADED, PENDING, UP, DISABLED,
   then by name. Problem branches must be visually dominant; healthy ones recede.

BRANCH CARD (design the card and a dense one-line table row variant for 50+ branches)
Show: branch name, IP/hostname in monospace, status (colour + label + icon), current latency,
packet loss, 24h uptime, and a tiny sparkline of the last hour's latency. For an open
incident show "Down for 14m" in place of the location line, plus "Not acknowledged" or
"Acknowledged by @alex". The whole card is one link to the branch detail page. A card for a
DOWN branch has a stronger border and a gentle pulse on its status dot.

STATES TO DESIGN (all of them, with the sample data)
- Normal mixed state (the sample data above).
- All healthy: calm and quiet, a single reassuring line like "All 7 branches are up", not a
  wall of green.
- Incident-heavy: 1 down, 1 degraded rise to the top.
- Monitor-suspect banner shown above the list (see the shell prompt).
- Empty: no branches yet, with a clear "Add your first branch" action.
- Filter returns nothing: "No branches match your filters" with a "Clear filters" action.
- Loading (skeleton cards, no spinner-only page) and error ("Could not load branches" + Retry).
- Live feed disconnected: values visibly marked as stale.
- Live update moment: a value changing latency, and a card changing status from UP to DOWN
  (show the transition treatment).
- Dense view with about 60 branches, still scannable.

MOBILE
Single column, summary bar as a compact 4-up row, search and a horizontally scrollable chip
row, cards simplified to name, status, latency, uptime. The most important thing, anything
down, must be visible without scrolling.
```

---

## 4. Branch detail

```
Design the detail page for one branch. It is reached from the Overview and from an alert.
Its job: answer "what is happening here, since when, and is it getting better?" then offer
history for diagnosis.

LAYOUT (desktop)
1. Header: back link to Overview, branch name (large), address in monospace
   ("10.20.2.1 · ICMP" or "10.20.4.1:443 · TCP"), optional location, a status pill, and an
   Edit button.
2. Incident banner (only when an incident is open): severity, start time, running duration
   ("Down since 14:02, 14m"), acknowledgement state, and the next action. Strong, but
   proportionate: DEGRADED is an amber warning, DOWN is red.
3. Metric tiles: Latency now, Packet loss now, Uptime 24h, Uptime 7d, Uptime 30d. Each with a
   label and a tabular number. Uptime never rounds up to 100% while any check failed (99.99%
   at most).
4. History section with a range switcher (1h, 24h, 7d) and two charts sharing one time axis:
   - Latency over time: line or area, a dashed horizontal line at the branch's latency warning
     threshold, gaps where the branch did not reply (a gap is data, not an error), hover
     tooltip with exact time and value.
   - Packet loss over time: bars from 0 to 100%.
   Shade DOWN periods subtly across both charts so incidents are visible at a glance.
5. Incident history table: severity, started, duration, state (Open / Resolved / Acknowledged
   by @alex). Newest first.
6. A small "Configuration" summary: probe type, warning thresholds, enabled or not.

STATES TO DESIGN
- Healthy branch (Head Office) with a flat, quiet chart.
- Degraded branch (North Depot): latency crossing the threshold line.
- Down branch (Riverside Branch): open incident banner, chart with a gap and shaded outage.
- TCP branch (Warehouse 2) showing the port.
- Pending branch (no data yet): friendly empty chart "Data appears after the first probe
  cycle".
- Disabled branch: clearly marked "Monitoring is off", with an Enable action.
- Branch not found (404) with a link back.
- Loading skeletons and error with Retry; each of the three ranges.

MOBILE
Tiles in a 2-column grid, charts full width and at least 200px tall, range switcher sticky near
the charts, incident table becomes stacked rows.
```

---

## 5. Branches (manage the monitored list)

```
Design the screen where the admin adds, edits, disables and deletes monitored branches. A new
admin should be able to add a branch in under a minute.

LAYOUT
- Page title "Branches" with a primary "Add branch" button.
- A table (desktop) or stacked cards (mobile) with: name (with location underneath), address
  (IP or hostname, plus :port for TCP), probe type (ICMP / TCP), warning thresholds
  ("Warn at 200 ms / 20%"), current status pill, and row actions Edit and Delete.
- Disabled branches appear greyed with a "Disabled" label and an Enable shortcut.

ADD / EDIT DIALOG (a modal on desktop, a full-height bottom sheet on mobile)
Fields: Name (required, unique, max 80), Gateway IP or hostname (required, monospace),
Location (optional), Probe type (ICMP ping or TCP connect), Port (only visible and required for
TCP), Warn above latency in ms (default 200), Warn above packet loss in % (default 20), and an
Enabled switch. Short helper text under thresholds explaining what they do ("Status becomes
Degraded when average latency stays above this"). Primary "Add branch" / "Save changes",
secondary Cancel.

VALIDATION STATES (design each)
- Inline field errors: "Invalid IP address or hostname", "A branch with this name already
  exists", "Port is required for TCP probes", "Must be between 0 and 100".
- Server error at the top of the dialog; saving state with the button disabled.
- Success: dialog closes and the new row briefly highlights, then shows PENDING.

DELETE CONFIRMATION
A dialog that names the branch and says exactly what is lost: "Its check history and incidents
will be permanently removed. To keep the history, disable the branch instead." Offer
"Disable instead" as the first, safe choice and a red "Delete branch" as the last. Destructive
action must not be the default focus.

STATES TO DESIGN
Populated table; empty ("No branches yet"); add dialog empty; add dialog with errors; edit
dialog; TCP variant with the port field; delete confirmation; mobile list and bottom sheet.
```

---

## 6. Incidents

```
Design the Incidents screen: a log of every period a branch was DOWN or DEGRADED, for
reviewing reliability and reporting.

LAYOUT
- Title "Incidents" with an "Open only" toggle, a branch filter (dropdown with search), a
  severity filter (Down / Degraded) and a date-range filter (24h, 7d, 30d, custom).
- A small summary above the table: "3 incidents in the last 7 days · 1 open · average
  duration 6m".
- A table: branch (links to its detail page), severity pill, started (absolute time plus
  "2h ago"), duration (open incidents show a live-ticking "14m and counting"), and state:
  Open / Resolved, with "Acknowledged by @alex at 14:09" when applicable.
- Open incidents are pinned to the top and visually stronger than resolved ones.

STATES TO DESIGN
- Mixed list (use the sample incidents).
- No incidents at all: positive and calm ("No incidents recorded yet").
- "Open only" with nothing open: "Everything is running normally."
- Filters returning nothing; loading; error with Retry.
- Long list with pagination or "Load more" (design the control).
- Mobile: each incident as a stacked card with the branch and severity as the headline.
```

---

## 7. Settings (new screen, not built yet)

```
Design a Settings screen. Today these values are server configuration files; this screen would
let the admin change them from the dashboard. Group them in a left-hand section list
(desktop) / stacked sections (mobile). Every setting shows its current value, a plain-language
explanation, its default, and a reset-to-default action. Saving shows a clear confirmation, and
changes that need a restart say so.

SECTIONS
1. Monitoring
   - Probe interval (seconds, default 30). Confirmations required before a status change
     (default 3) with a live explanation: "Changes are confirmed after 3 matching checks, about
     90 seconds, so one dropped packet never raises an alert."
   - Default warning thresholds for new branches (latency ms, loss %).
   - Canary target: host, port, type (TCP / ICMP). Explain it: "Used to tell a branch outage
     from a problem with the monitor's own network."
2. Alerts
   - Telegram connection status (connected as @bot_name, last message sent time), and a
     "Send test message" button with a result toast.
   - Authorised chat IDs: a list with label and ID, add and remove. Only these chats can use
     the bot. Removing the last one shows a warning.
   - Reminder interval (minutes, default 30, 0 = off).
3. Monitor health
   - Read-only status panel: probe loop (last cycle 6s ago), database, heartbeat service
     (last ping 41s ago) with green, amber or red states and what each means.
4. Account
   - Signed-in email, change password (current, new, repeat), sign out, theme preference.

STATES TO DESIGN
Each section populated; unsaved-changes bar ("2 unsaved changes · Save · Discard"); validation
errors; save success; Telegram test success and failure; heartbeat not configured ("Not set up"
with a link to instructions); mobile.
```

---

## 8. Telegram alert messages (bonus: the product's other screen)

```
Design the Telegram messages the bot sends, as phone screenshots in Telegram's chat style,
dark and light. Telegram allows plain text, a little emoji and up to a few inline buttons per
message, so the design work is in the text layout and button choices.

MESSAGES TO DESIGN
1. DOWN alert: "🔴 DOWN: Riverside Branch", then IP, latency ("no reply"), loss, time. Buttons:
   [✅ Acknowledge] [🔕 Mute 1h].
2. DEGRADED alert: "🟠 DEGRADED: North Depot", with 312 ms and 8% loss. Same buttons.
3. The same DOWN message after someone taps Acknowledge: the text gains
   "✅ Acknowledged by @alex at 14:09" and the Acknowledge button disappears.
4. After tapping Mute: the Mute button becomes [🔔 Unmute].
5. RECOVERED: "🟢 RECOVERED: Riverside Branch" with the outage duration. No buttons.
6. REMINDER (every 30 min while open): "⏰ STILL DOWN: Riverside Branch, ongoing for 1h 5m".
7. MONITOR MAY BE OFFLINE: "⚠️ MONITOR MAY BE OFFLINE", the explanation, and the time.
8. /status reply: one line per branch with a status dot and latest latency, a one-line summary
   on top ("5 up, 1 degraded, 1 down"), muted branches marked 🔕.
9. /branch Riverside reply: status, IP, latency, loss, 24h uptime, last incident, mute state.

GOALS
The first line must be understandable from the phone lock-screen notification preview: status,
branch name, nothing else. Keep every message under about 6 short lines. Use the same status
colours/emoji consistently.
```

---

## Follow-up prompts for iteration

```
Make the Overview denser: I have 80 branches. Keep problems prominent but fit more on screen.
```
```
Show me the Overview and Branch detail in light theme and check contrast of all status colours
against their backgrounds; list any pairing below 4.5:1 and fix it.
```
```
Turn the final designs into a component inventory: tokens, status pill, metric tile, branch
card, table row, dialog, form field, chip, toast, banner, with every state of each.
```
```
Design the loading, empty and error state for every screen as a single consistent pattern.
```
