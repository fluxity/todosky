"""One sync cycle: fetch both sides, reconcile, apply, persist."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import logging

from .config import Config, ConfigError
from .reconcile import (
    Action,
    CloseTodoist,
    CompleteChore,
    CreateChore,
    DeleteChore,
    ForgetLink,
    UpdateChore,
    reconcile,
)
from .skylight import SkylightAuthError, SkylightClient, SkylightError, SkylightNotFound
from .state import ACTIVE, DONE, REMOVED, Link, State
from .todoist import TaskState, TodoistAuthError, TodoistClient, TodoistError

_LOGGER = logging.getLogger(__name__)
PRUNE_AFTER = timedelta(days=60)


class Syncer:
    def __init__(self, config: Config, state: State, skylight: SkylightClient, todoist: TodoistClient) -> None:
        self.config = config
        self.state = state
        self.skylight = skylight
        self.todoist = todoist

    async def resolve_ids(self) -> tuple[str, str]:
        """Find the frame and the category for the configured person, cached between runs."""
        frame_id = self.config.skylight_frame_id or self.state.get("frame_id")
        if not frame_id:
            frames = await self.skylight.list_frames()
            if len(frames) != 1:
                ids = ", ".join(str(f.get("id")) for f in frames) or "none"
                raise ConfigError(f"Set SKYLIGHT_FRAME_ID; this account has these frames: {ids}")
            frame_id = str(frames[0]["id"])
            self.state.put("frame_id", frame_id)

        cache_key = f"category:{frame_id}:{self.config.skylight_category.lower()}"
        category_id = self.state.get(cache_key)
        if not category_id:
            categories = await self.skylight.list_categories(frame_id)
            wanted = self.config.skylight_category.strip().lower()
            match = [c for c in categories if c.label.strip().lower() == wanted]
            if not match:
                labels = ", ".join(c.label for c in categories)
                raise ConfigError(f"No Skylight profile named {self.config.skylight_category!r}. Found: {labels}")
            category_id = match[0].id
            self.state.put(cache_key, category_id)
        return frame_id, category_id

    async def run_once(self, *, dry_run: bool = False, today: date | None = None) -> list[Action]:
        today = today or date.today()
        frame_id, category_id = await self.resolve_ids()

        tasks = await self.todoist.list_tasks(self.config.todoist_filter)
        links = self.state.links()
        active = [link for link in links if link.status == ACTIVE]

        task_ids = {t.id for t in tasks}
        gone: dict[str, TaskState] = {}
        for todoist_id in {link.todoist_id for link in active} - task_ids:
            try:
                gone[todoist_id] = await self.todoist.task_state(todoist_id)
            except TodoistAuthError:
                raise
            except TodoistError as err:
                _LOGGER.warning("Could not look up Todoist task %s: %s", todoist_id, err)

        dates = [link.due_date for link in active] + [today]
        chores = {}
        if active:
            listed = await self.skylight.list_chores(frame_id, min(dates), max(dates))
            chores = {c.id: c for c in listed}

        actions = reconcile(tasks, chores, links, gone)
        for action in actions:
            if dry_run:
                _LOGGER.info("[dry run] %s", action.describe())
                continue
            try:
                await self._apply(action, frame_id, category_id)
                _LOGGER.info("%s", action.describe())
            except (SkylightAuthError, TodoistAuthError):
                raise
            except (SkylightError, TodoistError) as err:
                _LOGGER.error("Failed to %s: %s", action.describe(), err)

        if not dry_run:
            self.state.prune(today - PRUNE_AFTER)
        return actions

    async def _apply(self, action: Action, frame_id: str, category_id: str) -> None:
        state = self.state
        if isinstance(action, CreateChore):
            task = action.task
            chore = await self.skylight.create_chore(frame_id, category_id, task.content, task.due_date, task.due_time)
            state.upsert(Link(task.id, task.due_date, chore.id, task.content, task.due_time))

        elif isinstance(action, UpdateChore):
            link, task = action.link, action.task
            try:
                await self.skylight.update_chore(
                    frame_id, link.skylight_id, task.content, task.due_date, task.due_time
                )
            except SkylightNotFound:
                state.upsert(replace(link, status=REMOVED))
                return
            if task.due_date != link.due_date:
                state.delete(link.todoist_id, link.due_date)
            state.upsert(Link(task.id, task.due_date, link.skylight_id, task.content, task.due_time))

        elif isinstance(action, CloseTodoist):
            if action.close:
                await self.todoist.close_task(action.link.todoist_id)
            state.upsert(replace(action.link, status=DONE))

        elif isinstance(action, CompleteChore):
            try:
                await self.skylight.complete_chore(frame_id, action.link.skylight_id, date.today())
            except SkylightNotFound:
                pass
            except SkylightAuthError:
                raise
            except SkylightError as err:
                _LOGGER.warning("Completing the chore failed (%s); deleting it instead", err)
                await self.skylight.delete_chore(frame_id, action.link.skylight_id)
            state.delete(action.link.todoist_id, action.link.due_date)

        elif isinstance(action, DeleteChore):
            await self.skylight.delete_chore(frame_id, action.link.skylight_id)
            state.delete(action.link.todoist_id, action.link.due_date)

        elif isinstance(action, ForgetLink):
            state.upsert(replace(action.link, status=REMOVED))
