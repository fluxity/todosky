from datetime import date, datetime, time, timezone

from todosky.todoist import parse_due, parse_task


def test_date_only():
    assert parse_due({"date": "2026-11-09"}) == (date(2026, 11, 9), None)


def test_floating_datetime_in_date_field():
    assert parse_due({"date": "2026-11-09T18:30:00"}) == (date(2026, 11, 9), time(18, 30))


def test_utc_datetime_converted_to_local():
    moment = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc).astimezone()
    got = parse_due({"date": "2026-11-09T15:00:00Z", "datetime": "2026-11-09T15:00:00Z"})
    assert got == (moment.date(), moment.time())


def test_no_due():
    assert parse_due(None) is None
    assert parse_task({"id": "1", "content": "x", "due": None}) is None


def test_parse_task_recurring():
    task = parse_task({"id": 5, "content": "Bins", "due": {"date": "2026-11-09", "is_recurring": True}})
    assert task.id == "5" and task.is_recurring and task.due_date == date(2026, 11, 9)
