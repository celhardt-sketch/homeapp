"""Reminder emails for medication reorders and recurring home upkeep.

Delivery is configured by environment variables; whichever is present is used:
  RESEND_API_KEY                         -> https://resend.com API
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS -> plain SMTP (STARTTLS), e.g. Gmail app password
Sender address comes from REMINDER_FROM (default onboarding@resend.dev for Resend, SMTP_USER for SMTP).
The recipient is stored in settings (key reminder_email), editable from the admin panel,
with REMINDER_EMAIL as the initial value.
"""

import asyncio
import json
import logging
import os
import smtplib
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from .db import get_conn

log = logging.getLogger("notify")

CHECK_INTERVAL_SECONDS = 60 * 60


def email_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST"))


def send_email(to: str, subject: str, text: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if api_key:
        payload = json.dumps(
            {
                "from": os.environ.get("REMINDER_FROM", "Home Maintenance <onboarding@resend.dev>"),
                "to": [to],
                "subject": subject,
                "text": text,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return

    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("No email provider configured (set RESEND_API_KEY or SMTP_HOST)")
    user = os.environ.get("SMTP_USER", "")
    msg = EmailMessage()
    msg["From"] = os.environ.get("REMINDER_FROM", user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, os.environ.get("SMTP_PASS", ""))
        smtp.send_message(msg)


def get_reminder_email() -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'reminder_email'").fetchone()
    return row["value"] if row else os.environ.get("REMINDER_EMAIL", "")


def due_pickups() -> list[dict]:
    """Latest pickup per active medication whose reorder date has arrived and hasn't been emailed."""
    today = date.today()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id AS pickup_id, p.picked_up_on, p.reminder_sent_at,
                   m.id AS medication_id, m.name, m.person, m.reorder_days
            FROM medications m
            JOIN med_pickups p ON p.id = (
                SELECT id FROM med_pickups WHERE medication_id = m.id ORDER BY picked_up_on DESC, id DESC LIMIT 1
            )
            WHERE m.active = 1 AND p.reminder_sent_at IS NULL
            """
        ).fetchall()
    out = []
    for r in rows:
        reorder_on = date.fromisoformat(r["picked_up_on"]) + timedelta(days=r["reorder_days"])
        if reorder_on <= today:
            out.append({**dict(r), "reorder_on": reorder_on.isoformat()})
    return out


def due_upkeep() -> list[dict]:
    """Latest log per active upkeep item whose next-due date has arrived and hasn't been emailed.

    Items that have never been logged have no schedule yet and are only flagged in-app.
    """
    today = date.today()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.id AS item_id, i.name, i.category, i.interval_days,
                   l.id AS log_id, l.done_on, l.reminder_sent_at
            FROM upkeep_items i
            LEFT JOIN upkeep_logs l ON l.id = (
                SELECT id FROM upkeep_logs WHERE item_id = i.id ORDER BY done_on DESC, id DESC LIMIT 1
            )
            WHERE i.active = 1 AND l.id IS NOT NULL AND l.reminder_sent_at IS NULL
            """
        ).fetchall()
    out = []
    for r in rows:
        due_on = date.fromisoformat(r["done_on"]) + timedelta(days=r["interval_days"])
        if due_on <= today:
            out.append({**dict(r), "due_on": due_on.isoformat()})
    return out


def send_due_reminders() -> int:
    if not email_configured():
        return 0
    to = get_reminder_email()
    if not to:
        return 0
    meds = due_pickups()
    upkeep = due_upkeep()
    if not meds and not upkeep:
        return 0
    sections = []
    if meds:
        sections.append(
            "Time to reorder these prescriptions:\n"
            + "\n".join(
                f"- {d['name']} for {d['person']} (picked up {d['picked_up_on']}, reorder was due {d['reorder_on']})"
                for d in meds
            )
        )
    if upkeep:
        sections.append(
            "Home upkeep that's due:\n"
            + "\n".join(
                f"- {d['name']}" + (f" [{d['category']}]" if d["category"] else "") + f" (last done {d['done_on']}, due {d['due_on']})"
                for d in upkeep
            )
        )
    body = "\n\n".join(sections) + "\n\nOpen the app and mark each one done once it's taken care of."
    names = [d["name"] for d in meds] + [d["name"] for d in upkeep]
    subject = f"Home reminder: {', '.join(names[:3])}" + (f" +{len(names) - 3} more" if len(names) > 3 else "")
    send_email(to, subject, body)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.executemany(
            "UPDATE med_pickups SET reminder_sent_at = ? WHERE id = ?",
            [(now, d["pickup_id"]) for d in meds],
        )
        conn.executemany(
            "UPDATE upkeep_logs SET reminder_sent_at = ? WHERE id = ?",
            [(now, d["log_id"]) for d in upkeep],
        )
    return len(names)


async def reminder_loop() -> None:
    while True:
        try:
            sent = await asyncio.to_thread(send_due_reminders)
            if sent:
                log.info("Sent reminder for %d item(s)", sent)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
