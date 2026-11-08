"""Whole sync cycles against in-memory fakes of both services."""

from dataclasses import replace
from datetime import date, time
import itertools

import pytest

from todosky.config import Config
from todosky.skylight import Category, Chore, SkylightNotFound, Tokens
from todosky.state import ACTIVE, DONE, State
from todosky.sync import Syncer
from todosky.todoist import TaskState, TodoistTask

TODAY = date(2026, 11, 8)


class FakeSkylight:
    def __init__(self):
        self.chores: dict[str, Chore] = {}
        self.ids = itertools.count(1)

    async def list_frames(self):
        return [{"id": "9"}]

    async def list_categories(self, frame_id):
        return [Category("1", "Mom"), Category("2", "Dad")]

    async def list_chores(self, frame_id, after, before):
        return [c for c in self.chores.values() if c.start and after <= c.start <= before]

    async def create_chore(self, frame_id, category_id, summary, day, start_time):
        chore = Chore(f"c{next(self.ids)}", summary, category_id, day, start_time, "pending")
        self.chores[chore.id] = chore
        return chore

    async def update_chore(self, frame_id, chore_id, summary, day, start_time):
        if chore_id not in self.chores:
            raise SkylightNotFound(chore_id)
        self.chores[chore_id] = replace(self.chores[chore_id], summary=summary, start=day, start_time=start_time)

    async def complete_chore(self, frame_id, chore_id, day=None):
        self.chores[chore_id] = replace(self.chores[chore_id], status="complete")

    async def delete_chore(self, frame_id, chore_id):
        self.chores.pop(chore_id, None)


class FakeTodoist:
    def __init__(self):
        self.tasks: dict[str, TodoistTask] = {}
        self.completed: set[str] = set()
        self.closed: list[str] = []

    async def list_tasks(self, query):
        return list(self.tasks.values())

    async def task_state(self, task_id):
        return TaskState(exists=task_id in self.completed, completed=task_id in self.completed)

    async def close_task(self, task_id):
        self.closed.append(task_id)
        task = self.tasks[task_id]
        if task.is_recurring:
            self.tasks[task_id] = replace(task, due_date=date.fromordinal(task.due_date.toordinal() + 1))
        else:
            del self.tasks[task_id]
            self.completed.add(task_id)


@pytest.fixture
def env(tmp_path):
    config = Config("tok", "e", "p", state_path=tmp_path / "s.db")
    state = State(config.state_path)
    sky, todo = FakeSkylight(), FakeTodoist()
    yield Syncer(config, state, sky, todo), sky, todo, state
    state.close()


async def cycle(syncer, **kw):
    return await syncer.run_once(today=TODAY, **kw)


async def test_full_lifecycle(env):
    syncer, sky, todo, state = env
    todo.tasks["t1"] = TodoistTask("t1", "Bins", date(2026, 11, 9), None, False)

    await cycle(syncer)
    [chore] = sky.chores.values()
    assert (chore.summary, chore.category_id, chore.start) == ("Bins", "2", date(2026, 11, 9))
    assert await cycle(syncer) == []  # idempotent

    todo.tasks["t1"] = TodoistTask("t1", "Big bins", TODAY, time(18, 0), False)
    await cycle(syncer)
    chore = sky.chores[chore.id]
    assert (chore.summary, chore.start, chore.start_time) == ("Big bins", TODAY, time(18, 0))
    assert [link.due_date for link in state.links()] == [TODAY]

    await sky.complete_chore("9", chore.id, TODAY)
    await cycle(syncer)
    assert todo.closed == ["t1"]
    assert [link.status for link in state.links()] == [DONE]
    assert await cycle(syncer) == []


async def test_recurring_occurrences(env):
    syncer, sky, todo, state = env
    todo.tasks["r"] = TodoistTask("r", "Feed cat", TODAY, None, True)
    await cycle(syncer)
    [first] = sky.chores.values()

    await sky.complete_chore("9", first.id, TODAY)
    await cycle(syncer)  # closes Todoist; task advances to tomorrow
    assert todo.tasks["r"].due_date == date(2026, 11, 9)
    await cycle(syncer)  # pushes tomorrow's occurrence
    pending = [c for c in sky.chores.values() if c.status == "pending"]
    assert [c.start for c in pending] == [date(2026, 11, 9)]
    assert sorted(link.status for link in state.links()) == [ACTIVE, DONE]


async def test_removed_from_filter_deletes_and_completed_completes(env):
    syncer, sky, todo, state = env
    todo.tasks["a"] = TodoistTask("a", "A", TODAY, None, False)
    todo.tasks["b"] = TodoistTask("b", "B", TODAY, None, False)
    await cycle(syncer)

    del todo.tasks["a"]  # label removed
    del todo.tasks["b"]
    todo.completed.add("b")  # completed in Todoist
    await cycle(syncer)
    assert [c.summary for c in sky.chores.values()] == ["B"]
    assert [c.status for c in sky.chores.values()] == ["complete"]
    assert state.links() == []


async def test_dry_run_changes_nothing(env):
    syncer, sky, todo, state = env
    todo.tasks["t1"] = TodoistTask("t1", "Bins", TODAY, None, False)
    actions = await cycle(syncer, dry_run=True)
    assert len(actions) == 1 and sky.chores == {} and state.links() == []


async def test_tokens_persist(env):
    _, _, _, state = env
    state.save_tokens(Tokens("a", "r", 123.0))
    assert state.tokens() == Tokens("a", "r", 123.0)
