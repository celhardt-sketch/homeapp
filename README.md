# Home Maintenance

Web app for tracking recurring maintenance tasks per room, opened by scanning an NFC tag.

- Each room has a URL like `https://your-app.example.com/r/kitchen`. Write that URL to an NFC tag (e.g. with the "NFC Tools" app on iPhone/Android, Write → URL) and stick it in the room.
- Scanning the tag opens the room's task list. Tap the circle to check a task off; the app records **who** did it (name saved on the device, asked once) and **when**.
- Tasks have a frequency (daily, weekly, monthly, …) and show Due / Overdue based on the last completion.
- Notes can be added to a task, optionally flagged "something needs to be purchased" — those show up on the **To Buy** page.
- **Manage** page (password-protected admin panel): add/edit/delete rooms and tasks, copy each room's NFC link, change the admin password. Checking off tasks and adding notes does not require login.

## Admin password

The first time the server starts it sets the admin password from the `ADMIN_PASSWORD` environment variable (default `admin`). Change it from the Manage page (key icon) — the stored password then lives in the database and `ADMIN_PASSWORD` is no longer consulted.

## Stack

React 19 + Vite + Tailwind (frontend), FastAPI + SQLite (backend). In production a single container serves both.

## Local development

```bash
# backend (port 8000)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=~/hm-data .venv/bin/uvicorn app.main:app --reload --port 8000

# frontend (port 5173, proxies /api to the backend)
npm install
npm run dev
```

`npm run lint` / `npm run build` for checks.

## Deploy (Railway or any Docker host)

The `Dockerfile` builds the frontend and serves it from FastAPI. Mount a persistent volume at `/data` (or set `DATA_DIR`) so the SQLite database survives redeploys. Set `ADMIN_PASSWORD` before the first start to pick the initial admin password. The server listens on `$PORT` (default 8000).
