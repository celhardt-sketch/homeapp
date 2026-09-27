---
name: homeapp-browser-testing
description: Run Home Maintenance locally and verify browser auth, pantry, and medication workflows.
---

# Local browser testing

Use the repository blueprint's dependency and startup commands. The frontend on port 5173 proxies `/api` to FastAPI on 8000. Set `DATA_DIR` locally; preserve existing seed records and use clearly named disposable QA records. Restart any backend process without reload after backend changes.

## Devin Secrets Needed

No external secret is required when testing a local database with known admin credentials. `ADMIN_PASSWORD` only seeds a new database; an existing database retains its stored password. Never assume changing that environment variable resets the password.

## Browser state and test data

- Use a fresh incognito window to test the first-load name prompt and logged-out Manage gate without disturbing another browser profile.
- Name entry is independent of admin login. Public completion, notes, pantry and medication flows do not require admin authentication.
- After password rotation tests, restore the initial password and verify it by logging out and back in.
- For medication overdue testing, the greatest pickup date determines status, not insertion order. Adding old history after today's pickup should not make the medication overdue. Delete the newer QA pickup or use a separate QA medication to test overdue.
- Keep pickup history expanded while adding a backdated pickup to verify it updates without collapse/reopen.
- For native date inputs, click month and day segments directly; multi-digit entry may auto-advance.
- Without `RESEND_API_KEY` or SMTP configuration, expect the amber configuration note and disabled Send test. Saving an address proves persistence, not email delivery.
