# Design system

The rules behind the dashboard, so new screens stay consistent. Generated with the UI/UX Pro Max
skill, then adjusted and verified for this product (see "Where we deviated").

## Direction

An **instrument panel**, not a marketing site: calm, dense and precise. When everything is fine the
screen is quiet and neutral. When something is wrong the broken thing is unmistakable and sorted to
the top. Style: *Minimalism and Swiss*, dark by default, light theme equally polished. Scale: 1 to
100 branches; cards below about 18 branches, a dense table above (user can switch).

## Colour

Tokens live in `frontend/src/index.css` (`:root` for light, `.dark` for dark). Components use the
token names (`bg-card`, `text-muted`, `text-down`), never raw hex values.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `bg` / `card` / `card2` | #0F172A / #1B2336 / #252E43 | #F1F5F9 / #FFFFFF / #EEF2F7 | page, surfaces, hover and nested |
| `line` / `line-strong` | #2A3550 / #64748B | #DDE3EC / #7C8BA1 | decorative dividers / control borders (3:1) |
| `text` / `muted` | #F8FAFC / #9AA8BC | #0F172A / #475569 | body / secondary text |
| `accent` / `primary` | #7DB3FF / #2563EB | #1D4ED8 / #1D4ED8 | links and focus / buttons |
| `up` | #34D399 | #13722F | healthy |
| `degraded` | #FBBF24 | #8A5208 | slow or lossy |
| `down` | #FA8585 | #B91C1C | outage |
| `pending` | #9AA8BC | #475569 | new or disabled |

Rules:

- **Green means "up" and nothing else.** Buttons and links use the blue accent so an action is
  never mistaken for a status.
- **Status is never colour alone.** Every status has its own icon shape (check, triangle, cross,
  clock, pause) plus a text label. Charts mark outages with a shaded band and a text summary.
- Every text/background pair, including a status tint stacked on another tint, was checked for
  4.5:1 (text) and 3:1 (control borders) in both themes. Re-check if you change a token.
- Do not lower text contrast with `opacity-*`; use a different token.

## Type

**Fira Sans** for the interface, **Fira Code** for IP addresses and hostnames (`.mono`), bundled
through `@fontsource` so the dashboard works on a network without internet. Every figure that
changes uses `.num` (tabular numerals) so values do not jitter as they refresh. Base size 15px,
minimum 12px.

## Layout and touch

- Mobile first: 375px phone, 768px tablet, 1440px desktop (`max-w-[1440px]`).
- Phones get a bottom tab bar; tables become stacked cards. No horizontal page scroll.
- Every control on a phone is at least **44px** tall (`.btn`, `.icon-btn`, `.chip`, `.input`).
- Wide tables that must scroll sideways are keyboard-focusable (`tabIndex={0}` with a label).

## Navigation

- **Desktop: an auto-hiding sidebar.** A slim 56px icon rail is always visible; hovering it (with a
  short intent delay, so a pointer merely passing the edge does not open it) or tabbing into it slides
  out the full 240px sidebar **over** the page, so content and charts never shift. A pin button keeps
  it open and pushes the content over. Touch-only screens, which cannot hover, start pinned.
- Keyboard: only *keyboard* focus opens it (a mouse click must not leave it stuck open). Esc closes
  it and moves focus to the page. The first Tab stop is a "Skip to main content" link.
- Labels fade out when collapsed (opacity, not clipping, so no half letters show at the edge and
  screen readers still read them).
- **Phones:** a compact top header plus a bottom tab bar; the account dialog offers Sign out and Sign
  out of all devices.
- The sidebar bottom group holds: live status, the signed-in account, theme, Sign out, and Sign out of
  all devices (which asks first and focuses Cancel).
- Alerts that need attention stack above the page in an "Alerts" landmark: undelivered Telegram
  alerts, "monitor may be offline", then "N down / N degraded". The region is not rendered when empty.

## Components

Defined in `index.css` (`.card`, `.btn*`, `.icon-btn`, `.input`, `.chip`, `.th`, `.td`,
`.skeleton`, `.tone-*`, `.banner-*`) and `frontend/src/components/` (`StatusPill`, `Sparkline`,
`Modal`, `States`, `Layout`). Every data screen has loading (skeleton), empty (message plus an
action), error (announced, with Try again) and stale states.

## Behaviour rules

- **Trouble is shown; health is silent.** There is no "Live" badge. The sidebar (the header on phones)
  shows "Offline, reconnecting" or "Stale data" only when something is wrong, and screen readers
  announce it when it appears. Overview also shows
  "Updated 3s ago", and turns amber when no probe has arrived for about three intervals.
- **Screen readers hear changes, not noise.** A polite live region announces status *changes* only,
  never every 5-second refresh.
- Problems appear in the browser tab title, e.g. "(1 down) Branch Monitor".
- Forms: visible labels, errors under the field, focus moves to the first invalid field, helper text
  explains thresholds. Server errors map back to their field.
- Dialogs trap focus, close on Esc, and return focus to the button that opened them. Destructive
  confirmations offer the safe choice first ("Disable instead") and focus it by default.
- Motion is subtle (150 to 200 ms colour transitions); the only looping animation is a gentle pulse
  on Down, and everything respects `prefers-reduced-motion`.

## Where we deviated from the skill's output

- Its accent was green; we use blue, because green is reserved for status (above).
- Its "landing page" pattern does not apply to an application, so only its style, palette,
  typography and checklist were used.
- Fira fonts are bundled, not loaded from Google Fonts.
- Dark `down` is #FA8585, not #F87171: the original measured 4.33:1 when a status label sits on a
  down-tinted card.

## How this was verified

`scripts/demo_server.py` runs the real API and monitor against fake data (see the README), which
allowed screenshots of every screen in both themes at 1440px and 390px, an axe-core audit of every
screen and open dialog (zero violations after fixes), and scripted interaction checks (focus trap,
focus restore, validation, filters, theme persistence, login lockout countdown).
