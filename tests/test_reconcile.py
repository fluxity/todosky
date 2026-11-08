from datetime import date, time

from todosky.reconcile import (
    CloseTodoist,
    CompleteChore,
    CreateChore,
    DeleteChore,
    ForgetLink,
    UpdateChore,
    reconcile,
)
from todosky.skylight import Chore
from todosky.state import ACTIVE, DONE, REMOVED, Link
from todosky.todoist import TaskState, TodoistTask

D1, D2 = date(2026, 11, 9), date(2026, 11, 10)


def task(id="t1", content="Bins", due=D1, at=None, recurring=False):
    return TodoistTask(id, content, due, at, recurring)


def link(todoist_id="t1", due=D1, chore_id="c1", summary="Bins", at=None, status=ACTIVE):
    return Link(todoist_id, due, chore_id, summary, at, status)


def chore(id="c1", status="pending", day=D1):
    return Chore(id, "Bins", "dad", day, None, status)


def test_new_task_is_created():
    assert reconcile([task()], {}, [], {}) == [CreateChore(task())]


def test_unchanged_link_does_nothing():
    assert reconcile([task()], {"c1": chore()}, [link()], {}) == []


def test_rename_updates_chore():
    t = task(content="Take out bins")
    assert reconcile([t], {"c1": chore()}, [link()], {}) == [UpdateChore(link(), t)]


def test_time_added_updates_chore():
    t = task(at=time(9, 0))
    assert reconcile([t], {"c1": chore()}, [link()], {}) == [UpdateChore(link(), t)]


def test_one_off_reschedule_updates_chore():
    t = task(due=D2)
    assert reconcile([t], {"c1": chore()}, [link()], {}) == [UpdateChore(link(), t)]


def test_completed_on_skylight_closes_todoist():
    assert reconcile([task()], {"c1": chore(status="complete")}, [link()], {}) == [CloseTodoist(link(), close=True)]


def test_skipped_on_skylight_counts_as_done():
    assert reconcile([task()], {"c1": chore(status="skipped")}, [link()], {}) == [CloseTodoist(link(), close=True)]


def test_completed_on_both_sides_does_not_close_again():
    actions = reconcile([], {"c1": chore(status="complete")}, [link()], {"t1": TaskState(True, True)})
    assert actions == [CloseTodoist(link(), close=False)]


def test_completed_recurring_is_not_recreated_for_same_date():
    """Right after closing, Todoist still reports the old date in this cycle's fetch."""
    done = link(status=DONE)
    assert reconcile([task(recurring=True)], {"c1": chore(status="complete")}, [done], {}) == []


def test_recurring_next_occurrence_is_created():
    t = task(due=D2, recurring=True)
    assert reconcile([t], {}, [link(status=DONE)], {}) == [CreateChore(t)]


def test_recurring_completed_in_todoist_replaces_chore():
    t = task(due=D2, recurring=True)
    actions = reconcile([t], {"c1": chore()}, [link()], {})
    assert actions == [DeleteChore(link(), "recurring task moved to 2026-11-10"), CreateChore(t)]


def test_completed_in_todoist_completes_chore():
    assert reconcile([], {"c1": chore()}, [link()], {"t1": TaskState(True, True)}) == [CompleteChore(link())]


def test_deleted_in_todoist_deletes_chore():
    actions = reconcile([], {"c1": chore()}, [link()], {"t1": TaskState(False, False)})
    assert actions == [DeleteChore(link(), "no longer matches the Todoist filter")]


def test_label_removed_deletes_chore():
    actions = reconcile([], {"c1": chore()}, [link()], {"t1": TaskState(True, False)})
    assert actions == [DeleteChore(link(), "no longer matches the Todoist filter")]


def test_failed_lookup_leaves_link_alone():
    assert reconcile([], {"c1": chore()}, [link()], {}) == []


def test_chore_deleted_on_frame_is_not_recreated():
    assert reconcile([task()], {}, [link()], {}) == [ForgetLink(link())]


def test_tombstone_blocks_recreate_until_date_changes():
    tomb = link(status=REMOVED)
    assert reconcile([task()], {}, [tomb], {}) == []
    t = task(due=D2)
    assert reconcile([t], {}, [tomb], {}) == [CreateChore(t)]


def test_skylight_side_edit_is_not_overwritten():
    edited = Chore("c1", "Bins (done by kids?)", "dad", D1, None, "pending")
    assert reconcile([task()], {"c1": edited}, [link()], {}) == []
