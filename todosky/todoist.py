"""Minimal async client for the Todoist API v1."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Any

import aiohttp

BASE_URL = "https://api.todoist.com/api/v1"
TIMEOUT = aiohttp.ClientTimeout(total=30)


class TodoistError(Exception):
    pass


class TodoistAuthError(TodoistError):
    pass


@dataclass(frozen=True)
class TodoistTask:
    id: str
    content: str
    due_date: date
    due_time: dtime | None
    is_recurring: bool


@dataclass(frozen=True)
class TaskState:
    """What happened to a task that left the filter."""

    exists: bool
    completed: bool


def parse_due(due: Any) -> tuple[date, dtime | None] | None:
    """Turn a Todoist `due` object into a local date and optional time."""
    if not isinstance(due, dict):
        return None
    raw = due.get("datetime") or due.get("date")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    if len(raw) == 10:
        try:
            return date.fromisoformat(raw), None
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone()  # Show it at the local time of the machine running the sync.
    return moment.date(), moment.time().replace(second=0, microsecond=0)


def parse_task(item: dict[str, Any]) -> TodoistTask | None:
    due = parse_due(item.get("due"))
    if due is None:
        return None
    return TodoistTask(
        id=str(item["id"]),
        content=str(item.get("content") or ""),
        due_date=due[0],
        due_time=due[1],
        is_recurring=bool((item.get("due") or {}).get("is_recurring")),
    )


class TodoistClient:
    def __init__(self, session: aiohttp.ClientSession, token: str, *, base_url: str = BASE_URL) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {token}"}
        self._base_url = base_url.rstrip("/")

    async def _call(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        try:
            async with self._session.request(
                method, f"{self._base_url}{path}", headers=self._headers, timeout=TIMEOUT, **kwargs
            ) as resp:
                if resp.status in (401, 403):
                    raise TodoistAuthError(f"Todoist rejected the API token (HTTP {resp.status})")
                text = await resp.text()
                if resp.status >= 400 and resp.status != 404:
                    raise TodoistError(f"{method} {path} returned HTTP {resp.status}: {text[:200]}")
                return resp.status, (await resp.json(content_type=None)) if text.strip() else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TodoistError(f"Network error: {type(err).__name__}") from err

    async def list_tasks(self, query: str) -> list[TodoistTask]:
        tasks: list[TodoistTask] = []
        cursor: str | None = None
        while True:
            params = {"query": query, "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            status, payload = await self._call("GET", "/tasks/filter", params=params)
            if status == 404 or not isinstance(payload, dict):
                raise TodoistError("Unexpected response from /tasks/filter")
            for item in payload.get("results") or []:
                task = parse_task(item)
                if task:
                    tasks.append(task)
            cursor = payload.get("next_cursor")
            if not cursor:
                return tasks

    async def task_state(self, task_id: str) -> TaskState:
        status, payload = await self._call("GET", f"/tasks/{task_id}")
        if status == 404 or not isinstance(payload, dict) or payload.get("is_deleted"):
            return TaskState(exists=False, completed=False)
        return TaskState(exists=True, completed=bool(payload.get("checked")))

    async def close_task(self, task_id: str) -> None:
        """Complete a task. A recurring task moves to its next date instead."""
        await self._call("POST", f"/tasks/{task_id}/close")
