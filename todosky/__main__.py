"""Command line: `python -m todosky setup|once|run [--dry-run]`."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import sys

import aiohttp

from .config import Config, ConfigError
from .skylight import SkylightAuthError, SkylightClient, SkylightError
from .state import State
from .sync import Syncer
from .todoist import TodoistAuthError, TodoistClient, TodoistError

_LOGGER = logging.getLogger("todosky")
MAX_BACKOFF = 1800


@asynccontextmanager
async def make_syncer(config: Config):
    state = State(config.state_path)
    try:
        async with aiohttp.ClientSession() as session:
            skylight = SkylightClient(
                session,
                config.skylight_email,
                config.skylight_password,
                tokens=state.tokens(),
                on_tokens=state.save_tokens,
            )
            yield Syncer(config, state, skylight, TodoistClient(session, config.todoist_token))
    finally:
        state.close()


async def cmd_setup(config: Config) -> None:
    async with make_syncer(config) as syncer:
        frames = await syncer.skylight.list_frames()
        for frame in frames:
            name = (frame.get("attributes") or {}).get("name") or ""
            print(f"frame {frame.get('id')}  {name}")
        frame_id, category_id = await syncer.resolve_ids()
        print(f"\nUsing frame {frame_id}. Profiles:")
        for category in await syncer.skylight.list_categories(frame_id):
            marker = "  <- sync target" if category.id == category_id else ""
            print(f"  {category.id}  {category.label}{marker}")
        tasks = await syncer.todoist.list_tasks(config.todoist_filter)
        print(f"\nTodoist filter {config.todoist_filter!r} matches {len(tasks)} task(s):")
        for task in tasks:
            when = f"{task.due_date} {task.due_time or ''}".strip()
            print(f"  {when}  {task.content}{'  (recurring)' if task.is_recurring else ''}")


async def cmd_once(config: Config, dry_run: bool) -> None:
    async with make_syncer(config) as syncer:
        actions = await syncer.run_once(dry_run=dry_run)
        _LOGGER.info("%d action(s)%s", len(actions), " (dry run)" if dry_run else "")


async def cmd_run(config: Config) -> None:
    async with make_syncer(config) as syncer:
        delay = config.poll_seconds
        while True:
            try:
                actions = await syncer.run_once()
                if actions:
                    _LOGGER.info("%d action(s)", len(actions))
                delay = config.poll_seconds
            except (SkylightAuthError, TodoistAuthError, ConfigError):
                raise
            except (SkylightError, TodoistError) as err:
                delay = min(delay * 2, MAX_BACKOFF)
                _LOGGER.warning("Sync failed (%s); retrying in %ds", err, delay)
            await asyncio.sleep(delay)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todosky", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Show frames, profiles and matching Todoist tasks")
    once = sub.add_parser("once", help="Run one sync cycle")
    once.add_argument("--dry-run", action="store_true", help="Print what would change without changing it")
    sub.add_parser("run", help="Sync forever every POLL_SECONDS")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = Config.from_env()
        if args.command == "setup":
            asyncio.run(cmd_setup(config))
        elif args.command == "once":
            asyncio.run(cmd_once(config, args.dry_run))
        else:
            asyncio.run(cmd_run(config))
    except ConfigError as err:
        _LOGGER.error("%s", err)
        return 2
    except (SkylightAuthError, TodoistAuthError) as err:
        _LOGGER.error("Authentication failed: %s", err)
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
