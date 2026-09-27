# Standing rules for this app

These apply to every change. Set by the owner; do not relax them without being told to.

## Name matching
Dictated input contains no IDs ("we're down to half a bag of jasmine rice", "I finished the pink
bathroom"). Wherever an identifier is expected:
- Accept a name OR an id.
- Match case-insensitively, on substrings, tolerating plurals and misspellings
  (`backend/app/matching.py`: `resolve`, `similarity`). Nothing clever.
- Exactly one confident match: act.
- Several plausible matches: return `needs_disambiguation` with the candidates and their
  distinguishing detail, and change nothing.
- No match: say so and include the nearest few names.
- Never guess.

## Reminders persist
A reminder that fires once is how things get forgotten. Anything overdue stays in the due list until
someone marks it handled. It does not expire, get dismissed or get marked read. Notify the day it
becomes due, then daily while it remains due, escalating wording when urgent.

## Duplicates
Creating a record whose name closely matches an existing one returns the existing record flagged
as a likely duplicate (`duplicate: true`, HTTP 200) rather than inserting a second.

## Idempotency
Writes accept an optional `idempotency_key` and ignore a repeat of the same key within 10 minutes.
Do not rate-limit a burst of 20 writes.

## Batch by default
Anything done for several children or items at once takes an array. Ten calls for ten children is
a bug.

## Plain-language errors
Not stack traces, not bare codes. Example: "No room called 'garage'. Rooms are: Kitchen, Pink
Bathroom, Laundry Room, Carport, Little Boys Room, Big Boys Room, Girls Room, Downstairs."

## Tool descriptions are load-bearing
Claude picks MCP tools by reading them. Write each one the way a person would say the thing out
loud.

## A feature is not done until its MCP tools appear in tools/list
Every REST endpoint or UI feature gets matching tools in `backend/app/mcp_server.py`, registered in
`build_mcp()`, AND added to `EXPECTED_TOOLS` in `backend/tests/test_mcp.py`. That test compares the
live `tools/list` against the constant exactly, so a tool missing from either side fails the suite.

## Staleness over silence
Where a stored value goes out of date, return how old it is and flag it. A value presented as
current when it is stale is worse than no value, because it will be trusted.

## No silent defaults
If a required environment variable is missing, refuse to start and name it. Never seed a default
password.

## Roles
Two, plus the connector. Route dependencies: `MEMBER` (any signed-in person), `MANAGER`
(admin or connector), `ADMIN`.
- **ADMIN** (Courtney, Magnus): everything.
- **HELPER** (Susan, Vanessa; role `member`): identical permissions for both. Tasks, upkeep,
  pantry, shopping, children's sizes and needs, paperwork: full read and write. Prescriptions: log
  pickups, mark called, read refill status. Vehicles and appliances: read, update, complete
  renewals. Cannot manage users, passwords or rooms, cannot create or edit prescriptions, cannot
  add or deactivate vehicles or appliances.
- **CONNECTOR** (Claude, via OAuth at `/mcp`): full read and write on everything except
  `/api/admin/*`.

Every action records WHICH NAMED PERSON performed it. The activity feed and all completion and
pickup records show the person, never a role.

## Verifying
```
cd backend && .venv/bin/python -m pytest -q
npx tsc -b && npm run lint && npm run build
```
