"""Decide what a sync cycle should do. Pure: no I/O, so every rule is unit-tested."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .skylight import Chore
from .state import ACTIVE, Link
from .todoist import TaskState, TodoistTask


@dataclass(frozen=True)
class CreateChore:
    task: TodoistTask

    def describe(self) -> str:
        return f"create chore {self.task.content!r} on {self.task.due_date}"


@dataclass(frozen=True)
class UpdateChore:
    link: Link
    task: TodoistTask

    def describe(self) -> str:
        return f"update chore {self.link.summary!r} -> {self.task.content!r} on {self.task.due_date}"


@dataclass(frozen=True)
class CloseTodoist:
    """The chore was finished on Skylight. `close` is False when Todoist no longer needs closing."""

    link: Link
    close: bool

    def describe(self) -> str:
        verb = "complete Todoist task" if self.close else "record completion of"
        return f"{verb} {self.link.summary!r} ({self.link.due_date})"


@dataclass(frozen=True)
class CompleteChore:
    link: Link

    def describe(self) -> str:
        return f"complete chore {self.link.summary!r} ({self.link.due_date}), done in Todoist"


@dataclass(frozen=True)
class DeleteChore:
    link: Link
    reason: str

    def describe(self) -> str:
        return f"delete chore {self.link.summary!r} ({self.link.due_date}): {self.reason}"


@dataclass(frozen=True)
class ForgetLink:
    """The chore vanished from Skylight. Keep a tombstone so it isn't pushed again."""

    link: Link

    def describe(self) -> str:
        return f"chore {self.link.summary!r} ({self.link.due_date}) was removed on Skylight; not re-adding"


Action = CreateChore | UpdateChore | CloseTodoist | CompleteChore | DeleteChore | ForgetLink


def reconcile(
    tasks: Iterable[TodoistTask],
    chores: Mapping[str, Chore],
    links: Iterable[Link],
    gone: Mapping[str, TaskState],
) -> list[Action]:
    """Compare Todoist's tasks with the linked Skylight chores.

    tasks:  tasks currently matching the Todoist filter
    chores: Skylight chores by id, covering every active link's date
    links:  everything in the state store
    gone:   for active links whose task left the filter, what happened to the task
            (a missing entry means the lookup failed, so the link is left alone)
    """
    tasks_by_id = {t.id: t for t in tasks}
    links = list(links)
    known = {(link.todoist_id, link.due_date) for link in links}
    covered: set[str] = set()  # Todoist ids that keep a live chore after this cycle
    actions: list[Action] = []

    for link in links:
        if link.status != ACTIVE:
            continue
        task = tasks_by_id.get(link.todoist_id)
        chore = chores.get(link.skylight_id)

        # Completion on Skylight wins over everything, so a finished chore is never deleted.
        if chore is not None and chore.is_done:
            actions.append(CloseTodoist(link, close=task is not None and task.due_date == link.due_date))
            continue

        if chore is None:
            actions.append(ForgetLink(link))
            if task is not None and task.due_date == link.due_date:
                covered.add(task.id)  # Don't recreate what someone deleted on the frame.
            continue

        if task is None:
            state = gone.get(link.todoist_id)
            if state is None:
                covered.add(link.todoist_id)
            elif state.completed:
                actions.append(CompleteChore(link))
            else:
                actions.append(DeleteChore(link, "no longer matches the Todoist filter"))
            continue

        if task.due_date != link.due_date and task.is_recurring:
            # The occurrence was completed or skipped in Todoist; the next one is created below.
            actions.append(DeleteChore(link, f"recurring task moved to {task.due_date}"))
            continue

        covered.add(task.id)
        if task.due_date != link.due_date or task.content != link.summary or task.due_time != link.start_time:
            actions.append(UpdateChore(link, task))

    for task in tasks_by_id.values():
        if task.id not in covered and (task.id, task.due_date) not in known:
            actions.append(CreateChore(task))

    return actions
