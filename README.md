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
- **Pantry** page: track what's stocked; mark items "low" and they appear on To Buy.
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

On first start the server sets the passwords from `ADMIN_PASSWORD` (default `admin`) and `HOUSEHOLD_PASSWORD` (default `home`). Change them from the Manage page (key icon, admin only) — they then live in the database and the env vars are ignored. Until you do, the env var wins on every start — so setting `HOUSEHOLD_PASSWORD` after the first deploy still takes effect on the next restart. Changing a password logs out every device using that role.

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

The `Dockerfile` builds the frontend and serves it from FastAPI. Mount a persistent volume at `/data` (or set `DATA_DIR`) so the SQLite database survives redeploys. Set `ADMIN_PASSWORD` and `HOUSEHOLD_PASSWORD` before the first start to pick the initial passwords. The server listens on `$PORT` (default 8000).
