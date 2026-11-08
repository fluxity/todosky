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
docker run -d --name todosky --restart unless-stopped --env-file .env -e TZ=America/New_York -v todosky-data:/data todosky
docker logs -f todosky
```

- Set `TZ` to your local zone: timed Todoist tasks are shown at the local time of the machine running the sync.
- The state file lives on the `todosky-data` volume, so it survives restarts and upgrades. Don't remove the volume (see the warning about `todosky.db` above).
- Run only one copy of the sync at a time, either locally or in Docker. Two copies with separate state files will push duplicate chores. To move an existing local `todosky.db` into Docker, copy it onto the volume before the first start: `docker run --rm -v todosky-data:/data -v "$PWD":/src todosky cp /src/todosky.db /data/`.

## Kubernetes (k3s)

Deployed with Skaffold, in the same way as todopaper: the image is built for `linux/amd64` and `linux/arm/v7`, pushed to ECR (`381092307294.dkr.ecr.us-east-1.amazonaws.com/todosky:latest`), and pulled with the `ecr-push-cred` secret that todopaper's `ecr-secret-refresher` CronJob keeps fresh in `default`.

```bash
skaffold run
```

- Needs `docker` and `kubectl` working in the shell that runs Skaffold, plus AWS credentials for the ECR login hook.
- Before deploying, Skaffold creates or updates the `todosky` Secret from your local `.env`. Edit `.env` and run `skaffold run` again to change credentials or settings.
- State is kept on the `todosky-data` PersistentVolumeClaim (k3s `local-path`), so the pod stays on the node where the volume was first created. The deployment uses `Recreate`, so old and new pods never sync at the same time.
- Stop any local or Docker copy before deploying, or chores get pushed twice.
- Logs: `kubectl logs -f deploy/todosky`
- If the build fails at the end with `DENIED: Your authorization token has expired`, run `skaffold run` again. Skaffold reads the stored ECR token before its login hook replaces it, so the first run after 12 hours can fail.

## Tests

```bash
.venv/bin/pytest
```
