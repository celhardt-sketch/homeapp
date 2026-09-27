"""Reminders: prescription refills (in-app, emailed when configured) and recurring home upkeep emails.

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

from . import refills
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


def due_upkeep() -> list[dict]:
    """Latest log per active upkeep item whose next-due date has arrived and hasn't been emailed.

    Items that have never been logged are due in-app (and via MCP/status) but have no log
    row to record an email against, so they are not emailed.
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


def pending_assignment_notifications() -> list[dict]:
    """Notifications (assignments and refill reminders) not yet emailed, with the person's email (may be blank)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.kind, n.ref_id, n.message, n.created_by, n.created_at, n.read_at,
                   u.id AS user_id, u.name AS user_name, u.email
            FROM notifications n JOIN users u ON u.id = n.user_id
            WHERE n.emailed_at IS NULL
            ORDER BY n.created_at, n.id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def send_assignment_notifications() -> int:
    """Email each pending assignment to the assignee (one email per assignment, never a digest).
    People without an email address only get the in-app notification."""
    if not email_configured():
        return 0
    sent = 0
    for n in pending_assignment_notifications():
        if not n["email"]:
            continue
        send_email(n["email"], f"Home: {n['message']}", f"{n['message']}\n\nOpen the app to see your list.")
        with get_conn() as conn:
            conn.execute(
                "UPDATE notifications SET emailed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), n["id"]),
            )
        sent += 1
    return sent


def send_due_reminders() -> int:
    """Refill reminders are created as in-app notifications every day an item is due (even with no
    email configured); everything below that is email."""
    refills.create_due_notifications()
    if not email_configured():
        return 0
    sent_assignments = send_assignment_notifications()
    to = get_reminder_email()
    if not to:
        return sent_assignments
    upkeep = due_upkeep()
    if not upkeep:
        return sent_assignments
    sections = []
    if upkeep:
        sections.append(
            "Home upkeep that's due:\n"
            + "\n".join(
                f"- {d['name']}" + (f" [{d['category']}]" if d["category"] else "") + f" (last done {d['done_on']}, due {d['due_on']})"
                for d in upkeep
            )
        )
    body = "\n\n".join(sections) + "\n\nOpen the app and mark each one done once it's taken care of."
    names = [d["name"] for d in upkeep]
    subject = f"Home reminder: {', '.join(names[:3])}" + (f" +{len(names) - 3} more" if len(names) > 3 else "")
    send_email(to, subject, body)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.executemany(
            "UPDATE upkeep_logs SET reminder_sent_at = ? WHERE id = ?",
            [(now, d["log_id"]) for d in upkeep],
        )
    return len(names) + sent_assignments


async def reminder_loop() -> None:
    while True:
        try:
            sent = await asyncio.to_thread(send_due_reminders)
            if sent:
                log.info("Sent reminder for %d item(s)", sent)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
