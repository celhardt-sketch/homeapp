# Home Maintenance

Web app for tracking recurring maintenance tasks per room, opened by scanning an NFC tag.

- Each room has a URL like `https://your-app.example.com/r/kitchen`. Write that URL to an NFC tag (e.g. with the "NFC Tools" app on iPhone/Android, Write → URL) and stick it in the room.
- Scanning the tag opens the room's task list. Tap the circle to check a task off; the app records **who** did it (name saved on the device, asked once) and **when**.
- Tasks have a frequency (daily, weekly, monthly, …) and show Due / Overdue based on the last completion.
- Notes can be added to a task, optionally flagged "something needs to be purchased" — those show up on the **To Buy** page.
- The whole app is behind a password with two roles (see below). Every `/api/*` route except `/api/health` and `/api/login` returns 401 without a valid session token (deny-by-default middleware, so new routes are protected automatically); each route also declares the role it needs, and a household session on an admin-only route gets 403.
- NFC tags only ever point at a room *page* (`/r/<slug>`). Nothing is written from a URL alone — checking a task off is a POST made by a tap inside the app.
- **Manage** page: add/edit rooms and tasks, copy each room's NFC link; admin also deletes rooms, sets both passwords and reminder settings.

- **Upkeep** page: house-wide recurring jobs not tied to a room (HVAC filter, car oil change, window screens, gutters…). Each has a repeat interval; "Mark done" logs who/when and schedules the next reminder, shown in-app (Home page banner + Upkeep tab) and emailed.
- **Pantry** page: track what's stocked; mark items "low" and they appear on To Buy. An optional **par level** makes an item low automatically when its quantity (leading number, e.g. `2 bags`) is at or below it; an optional expiry date flags items expiring within a week. "Add many" adds a whole grocery run in one request. Adding a name that closely matches an existing item (case-insensitive, substring, or a typo) returns the existing item flagged `duplicate` instead of creating a second row.
- `GET /api/status` (household-readable) summarises tasks overdue / due today / due within 7 days, upkeep due, pantry below par and pantry expiring within 7 days. It never includes medication data (enforced by a test).
- **Meds** page: log each prescription pickup (date + who for). A reorder reminder is due 28 days later (adjustable), shown in-app and emailed.

## Reminder emails

The server checks hourly for medications past their reorder date and upkeep jobs past their due date, and emails the address set on the Manage page (or `REMINDER_EMAIL`). Configure one provider via environment variables:

- Resend: `RESEND_API_KEY` (and optionally `REMINDER_FROM`, default `onboarding@resend.dev` which only delivers to your own Resend account email until you verify a domain).
- SMTP (e.g. Gmail app password): `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, optional `REMINDER_FROM`.

Without either, reminders are still shown in the app.

## Passwords and roles

| | Household | Admin |
|---|---|---|
| Rooms, tasks, completions, notes, upkeep, pantry, shopping | read + write | read + write |
| Delete rooms, activity feed, medications, pickups, `/api/admin/*`, passwords | — (403) | yes |
| Session | 365 days, sliding (renewed on every request) | 30 days |

There are no default passwords. On first start the server sets the passwords from `ADMIN_PASSWORD` and `HOUSEHOLD_PASSWORD`; if either is missing (and no password is stored yet for that role) the server refuses to start and logs which variable is missing. Change them from the Manage page (key icon, admin only) — they then live in the database and the env vars are ignored. A stored password still equal to an old built-in default (`admin` / `home`) is treated as unset. Changing a password logs out every device using that role.

## Claude connector (MCP)

The server exposes a remote MCP server (Streamable HTTP) at `/mcp` so Claude can read and write rooms, tasks, upkeep, pantry and the shopping list. It runs in the same process and database as the app.

- **Connect:** in Claude, add a custom connector with the URL shown on the Manage page (`https://<your-domain>/mcp`). Claude registers itself (OAuth 2.1 dynamic client registration + PKCE) and opens an approval page that asks for the **admin** password once. No static API keys.
- **Role:** Claude acts as a third role, `connector` — same data as household, but it can never reach medications, pickups or `/api/admin/*`.
- **Revoke:** Manage → "Claude connector" → Disconnect. This deletes only the connector's OAuth tokens; family devices and the admin login stay signed in.
- **Public URL:** OAuth needs to know the public HTTPS address. On Railway this comes from `RAILWAY_PUBLIC_DOMAIN` automatically; elsewhere set `PUBLIC_URL=https://your-domain`.
- Tools accept names as people say them ("jasmine rice", "the pink bathroom"); when a name could mean several things the tool returns `needs_disambiguation` and changes nothing. Writes accept an `idempotency_key`.

## Stack

React 19 + Vite + Tailwind (frontend), FastAPI + SQLite (backend). In production a single container serves both.

## Local development

```bash
# backend (port 8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=~/hm-data .venv/bin/uvicorn app.main:app --reload --port 8000
# tests (auth coverage of every /api route):
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest

# frontend (port 5173, proxies /api to the backend)
npm install
npm run dev
```

`npm run lint` / `npm run build` for checks.

## Deploy (Railway or any Docker host)

The `Dockerfile` builds the frontend and serves it from FastAPI. Mount a persistent volume at `/data` (or set `DATA_DIR`) so the SQLite database survives redeploys. `ADMIN_PASSWORD` and `HOUSEHOLD_PASSWORD` are required for the first start (the app will not boot without them). The server listens on `$PORT` (default 8000).
