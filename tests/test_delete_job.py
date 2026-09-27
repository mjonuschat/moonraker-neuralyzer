import asyncio
import sqlite3

import neuralyzer
from neuralyzer import Rule, load_component


async def _seed_job(fake_history, job_id: int, status: str = "cancelled") -> None:
    def _insert(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO job_history VALUES (?, ?)", (job_id, status))
        conn.commit()

    await fake_history.history_table.queue_callback(_insert)


async def test_delete_job_removes_row_and_corrects_totals(make_config, fake_history):
    await _seed_job(fake_history, 26)
    fake_history.job_totals.update({"total_jobs": 5, "total_time": 500.0})
    neuralyzer = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 120.0, "print_duration": 90.0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    await neuralyzer._delete_job(26, job, rule)

    assert fake_history.job_totals["total_jobs"] == 4
    assert fake_history.job_totals["total_time"] == 380.0

    def _check(conn):
        return conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone()

    remaining = await fake_history.history_table.queue_callback(_check)
    assert remaining is None

    def _totals(conn):
        return conn.execute("SELECT total FROM job_totals WHERE field = 'total_jobs'").fetchone()

    row = await fake_history.history_table.queue_callback(_totals)
    assert row[0] == 4


async def test_delete_job_missing_row_reverts_in_memory_totals(make_config, fake_history):
    # No row seeded for job_id 26: DELETE affects zero rows.
    fake_history.job_totals.update({"total_jobs": 5, "total_time": 500.0})
    neuralyzer = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 120.0, "print_duration": 0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    await neuralyzer._delete_job(26, job, rule)

    # Deltas were applied in memory, then re-added on failure: net no change.
    assert fake_history.job_totals["total_jobs"] == 5
    assert fake_history.job_totals["total_time"] == 500.0

    def _totals(conn):
        return conn.execute("SELECT total FROM job_totals WHERE field = 'total_jobs'").fetchone()

    row = await fake_history.history_table.queue_callback(_totals)
    assert row[0] == 5  # the revert was persisted, not just kept in memory


async def test_delete_job_skips_reverting_job_totals_replaced_by_a_reset(make_config, fake_history):
    fake_history.job_totals.update({"total_jobs": 5, "total_time": 500.0})
    neuralyzer = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 120.0, "print_duration": 0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    # No job_history row for 26 -> forces a _PreCommitFailure. Pause the
    # queue so we can replace job_totals (simulating a totals reset)
    # while the delete is genuinely in flight, deterministically: since
    # _delete_job does all its synchronous prep (compute + apply delta,
    # build rows, call queue_callback) before its first await, creating
    # the task and yielding once with `await asyncio.sleep(0)` reliably
    # runs that prep to completion (asyncio schedules the new task's
    # first step before our sleep(0)'s own resumption), with no
    # dependence on wall-clock timing.
    fake_history.pause()
    task = asyncio.create_task(neuralyzer._delete_job(26, job, rule))
    await asyncio.sleep(0)  # let _delete_job apply its delta & enqueue
    assert fake_history.job_totals["total_jobs"] == 4  # delta already applied

    from neuralyzer import BASE_TOTALS

    fake_history.job_totals = dict(BASE_TOTALS)  # a reset replaces the dict

    fake_history.resume()
    await task

    # The reset's own (zeroed) totals must not have been touched by a
    # stale re-add of the pre-reset delta.
    assert fake_history.job_totals == BASE_TOTALS


async def test_delete_job_reapplies_aux_delta_into_a_list_replaced_by_a_normal_finish(
    make_config, fake_history
):
    """aux_totals is replaced by a NEW list object on every job finish,
    not only on a totals reset (see History._update_aux_totals()). A
    failed delete's compensation must still find and restore its aux
    delta — in memory AND on disk — in whatever aux_totals list is
    current, using the index captured before the await, gated solely
    on whether job_totals (the one reliable reset signal) was replaced."""

    def _seed_aux_row(conn):
        conn.execute(
            "INSERT INTO job_totals VALUES ('power_meter', 'energy', NULL, 10.0, 'default')"
        )
        conn.commit()

    await fake_history.history_table.queue_callback(_seed_aux_row)

    fake_history.aux_totals = [
        {"provider": "power_meter", "field": "energy", "maximum": None, "total": 10.0}
    ]
    neuralyzer = load_component(make_config({}))
    job = {
        "job_id": "000001",
        "total_duration": 0,
        "print_duration": 0,
        "filament_used": 0,
        "auxiliary_data": [{"provider": "power_meter", "name": "energy", "value": 4.0}],
    }
    rule = Rule(name="no_extrusion", source="", template=None)

    # No job_history row for 1 -> forces a _PreCommitFailure.
    fake_history.pause()
    task = asyncio.create_task(neuralyzer._delete_job(1, job, rule))
    await asyncio.sleep(0)  # let _delete_job apply its delta & enqueue (see note above)
    assert fake_history.aux_totals[0]["total"] == 6.0  # delta already applied

    # Simulate an unrelated job finishing concurrently: History replaces
    # the aux_totals list object, carrying the (already-decremented)
    # value forward in the same order, exactly like _update_aux_totals().
    fake_history.aux_totals = [dict(entry) for entry in fake_history.aux_totals]
    job_totals_before = fake_history.job_totals  # unchanged: not a reset

    fake_history.resume()
    await task

    assert fake_history.job_totals is job_totals_before
    assert fake_history.aux_totals[0]["total"] == 10.0

    def _read_aux_row(conn):
        return conn.execute(
            "SELECT total FROM job_totals WHERE provider = 'power_meter' AND field = 'energy'"
        ).fetchone()

    row = await fake_history.history_table.queue_callback(_read_aux_row)
    assert row[0] == 10.0  # the revert was persisted, not just kept in memory


async def test_delete_job_commit_failure_does_not_compensate(
    monkeypatch, make_config, fake_history
):
    """A _CommitFailure means the outcome on disk is unknown -- the DELETE
    may or may not have landed -- so _delete_job must log and leave the
    already-applied in-memory delta alone rather than risk re-adding it
    on top of a write that actually committed."""

    def _raise_commit_failure(conn, job_id, rows):
        raise neuralyzer._CommitFailure("simulated commit failure")

    monkeypatch.setattr(neuralyzer, "delete_and_correct", _raise_commit_failure)

    await _seed_job(fake_history, 26)
    fake_history.job_totals.update({"total_jobs": 5, "total_time": 500.0})
    component = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 120.0, "print_duration": 90.0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    await component._delete_job(26, job, rule)

    # Delta was applied once, never reverted: no compensation happened.
    assert fake_history.job_totals["total_jobs"] == 4
    assert fake_history.job_totals["total_time"] == 380.0

    def _check(conn):
        return conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone()

    # The row is still there -- delete_and_correct never actually ran --
    # proving no second queue_callback tried to persist a revert either.
    remaining = await fake_history.history_table.queue_callback(_check)
    assert remaining is not None


async def test_concurrent_history_write_during_a_successful_delete_loses_neither_update(
    make_config, fake_history
):
    """Spec requirement (Testing section): 'A History finish_job() that
    runs while a neuralyzer callback is in flight must not lose either
    update to the totals' -- success case. History's own totals write
    for an unrelated job can only run while neuralyzer's callback is
    suspended (single event loop), and its DB write is queued strictly
    after neuralyzer's own (the command queue is FIFO), so it must be
    based on neuralyzer's already-corrected in-memory values and must
    not be clobbered by (or clobber) neuralyzer's own persisted row."""

    def _seed(conn):
        conn.execute("INSERT INTO job_history VALUES (26, 'cancelled')")
        conn.commit()

    await fake_history.history_table.queue_callback(_seed)
    fake_history.job_totals.update({"total_jobs": 1, "total_time": 100.0})

    neuralyzer = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 100.0, "print_duration": 80.0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    fake_history.pause()
    task = asyncio.create_task(neuralyzer._delete_job(26, job, rule))
    await asyncio.sleep(0)  # neuralyzer's delete_and_correct is now queued (paused)
    assert fake_history.job_totals["total_jobs"] == 0
    assert fake_history.job_totals["total_time"] == 0.0

    # A competing History write for an unrelated job finishing
    # concurrently, adding its own contribution to the same in-memory
    # dict, then queuing its own DB write -- queued behind neuralyzer's
    # (still paused) callback, so it will run strictly after it.
    fake_history.job_totals["total_jobs"] += 1
    fake_history.job_totals["total_time"] += 50.0

    def _history_finish_write(conn):
        conn.execute(
            "UPDATE job_totals SET total = ? WHERE provider = 'history' AND field = 'total_jobs'",
            (fake_history.job_totals["total_jobs"],),
        )
        conn.execute(
            "UPDATE job_totals SET total = ? WHERE provider = 'history' AND field = 'total_time'",
            (fake_history.job_totals["total_time"],),
        )
        conn.commit()

    history_write = fake_history.history_table.queue_callback(_history_finish_write)

    fake_history.resume()
    await task
    await history_write

    # Final persisted totals reflect BOTH neuralyzer's correction and
    # History's own addition -- neither update was lost.
    def _read(conn):
        return dict(
            conn.execute(
                "SELECT field, total FROM job_totals WHERE provider = 'history'"
            ).fetchall()
        )

    persisted = await fake_history.history_table.queue_callback(_read)
    assert persisted["total_jobs"] == 1
    assert persisted["total_time"] == 50.0
    assert fake_history.job_totals["total_jobs"] == 1
    assert fake_history.job_totals["total_time"] == 50.0


async def test_concurrent_history_write_during_a_failed_delete_loses_neither_update(
    make_config, fake_history
):
    """Same spec requirement, failure case, with a genuinely queued
    competing write (not just an in-memory simulation): a History
    write queued while neuralyzer's failing callback #1 is still
    paused is guaranteed to occupy FIFO position #2, strictly ahead of
    neuralyzer's own revert-persist (call it #3), because #3 can only
    be queued *after* #1 has already been fully processed -- which is
    also required before the worker can ever reach #2. So #2 is always
    `put()` onto the queue before #3 can possibly exist, regardless of
    OS thread scheduling: `queue.Queue` preserves `put()` order, and
    #2's `put()` happens-before #1 even starts running, let alone
    finishes and triggers #3.

    The History callback's value is *captured on the event-loop
    thread* before queuing (not read from the live dict inside the
    callback body, which runs on the worker thread) — avoiding a
    Python-level data race against neuralyzer's own compensation code,
    which mutates that same dict on the event-loop thread once #1
    fails."""

    def _seed_row(conn):
        conn.execute(
            "UPDATE job_totals SET total = 5 WHERE provider = 'history' AND field = 'total_jobs'"
        )
        conn.commit()

    await fake_history.history_table.queue_callback(_seed_row)
    fake_history.job_totals["total_jobs"] = 5

    neuralyzer = load_component(make_config({}))
    job = {"job_id": "00001A", "total_duration": 0, "print_duration": 0, "filament_used": 0}
    rule = Rule(name="no_extrusion", source="", template=None)

    # No job_history row for 26 -> forces a _PreCommitFailure.
    fake_history.pause()
    task = asyncio.create_task(neuralyzer._delete_job(26, job, rule))
    await asyncio.sleep(0)  # neuralyzer's delta applied (total_jobs: 5 -> 4), callback #1 queued

    # A concurrent History finish_job() adds its own job. Its value is
    # captured now (event-loop thread) and its write queued as #2.
    history_value = fake_history.job_totals["total_jobs"] + 1  # 4 + 1 = 5
    fake_history.job_totals["total_jobs"] = history_value  # History's own in-memory update

    def _history_finish_write(conn):
        conn.execute(
            "UPDATE job_totals SET total = ? WHERE provider = 'history' AND field = 'total_jobs'",
            (history_value,),
        )
        conn.commit()

    history_write = fake_history.history_table.queue_callback(_history_finish_write)

    fake_history.resume()
    await task
    await history_write

    # Neuralyzer's compensation re-adds its delta (+1) on top of
    # whatever job_totals held at that point (5, including History's
    # addition), landing on 6 -- and its own revert-persist (#3),
    # queued strictly after History's write (#2), is the one that
    # lands last in the database. Neither update was lost.
    assert fake_history.job_totals["total_jobs"] == 6

    def _read(conn):
        return conn.execute(
            "SELECT total FROM job_totals WHERE provider = 'history' AND field = 'total_jobs'"
        ).fetchone()

    row = await fake_history.history_table.queue_callback(_read)
    assert row[0] == 6
