# todosky

Pushes Todoist chores to a Skylight frame and brings Skylight completions back to Todoist.

- Every Todoist task matching `#chores & @sky & !no date` appears as a chore under the **Dad** profile on its due date (with its time, if it has one).
- Ticking the chore on the Skylight completes the Todoist task. A recurring task moves to its next date, which is then pushed as a new chore.
- Renaming or rescheduling in Todoist updates the chore. Completing it in Todoist completes the chore; deleting it, removing `@sky`, moving it out of `#chores` or clearing its date removes the chore.
- A chore deleted on the Skylight is not pushed again unless the Todoist task gets a new date.
- Todoist is the source of truth for titles and dates; edits made on the Skylight side are left alone until the Todoist task changes.

> Skylight has no official API. Login and reading chores are ported from SkyLink. Creating, editing, completing and deleting chores match requests captured from the Skylight web app — see [docs/skylight-write-api.md](docs/skylight-write-api.md). Either can break if Skylight changes its API.

## Setup

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'
cp .env.example .env   # fill in tokens and password
.venv/bin/python -m todosky setup          # checks logins, shows the frame, profiles and matching tasks
.venv/bin/python -m todosky once --dry-run # shows what would change
.venv/bin/python -m todosky once           # one sync
.venv/bin/python -m todosky run            # sync every POLL_SECONDS
```

The Todoist token is under Todoist **Settings > Integrations > Developer**. The Skylight account needs a password (Google/Apple-only sign-in won't work).

State (links between tasks and chores, Skylight tokens) is kept in `todosky.db`. Deleting it makes the next run push every matching task again, creating duplicates of chores already on the frame.

## Docker

```bash
docker build -t todosky .
docker run -d --name todosky --restart unless-stopped --env-file .env -e TZ=America/Chicago -v todosky-data:/data todosky
```

Set `TZ` to your local zone: timed Todoist tasks are shown at the local time of the machine running the sync.

## Tests

```bash
.venv/bin/pytest
```
