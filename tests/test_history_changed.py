import sqlite3

from neuralyzer import load_component


def _job(job_id_hex: str, status: str, **extra) -> dict:
    return {"job_id": job_id_hex, "status": status, "filament_used": 0, **extra}


async def test_registers_event_handler(make_config, fake_history, fake_server):
    neuralyzer = load_component(make_config({}))
    assert neuralyzer._on_history_changed in fake_server._events["history:history_changed"]


async def test_ignores_non_finished_actions(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    called = []
    neuralyzer._delete_job = lambda *a, **k: called.append(a)
    await neuralyzer._on_history_changed({"action": "added", "job": _job("000001", "in_progress")})
    assert called == []


async def test_completed_job_kept_by_default_without_evaluating_rules(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    calls = []
    neuralyzer._delete_job = lambda *a, **k: calls.append(a)
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("000001", "completed", filament_used=0)}
    )
    assert calls == []


async def test_completed_job_evaluated_when_process_completed_enabled(make_config, fake_history):
    neuralyzer = load_component(make_config({"process_completed": "true"}))
    calls = []

    async def _fake_delete(job_id, job, rule):
        calls.append((job_id, rule.name))

    neuralyzer._delete_job = _fake_delete
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("000001", "completed", filament_used=0)}
    )
    assert calls == [(1, "no_extrusion")]


async def test_dry_run_never_deletes(make_config, fake_history):
    neuralyzer = load_component(make_config({"dry_run": "true"}))
    calls = []
    neuralyzer._delete_job = lambda *a, **k: calls.append(a)
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("000001", "cancelled", filament_used=0)}
    )
    assert calls == []


async def test_no_rule_match_keeps_job(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    calls = []
    neuralyzer._delete_job = lambda *a, **k: calls.append(a)
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("000001", "cancelled", filament_used=5)}
    )
    assert calls == []


async def test_job_id_removed_from_tracked_set_regardless_of_outcome(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    neuralyzer.tracked_jobs.add(1)
    neuralyzer._delete_job = lambda *a, **k: None
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("000001", "cancelled", filament_used=5)}
    )
    assert 1 not in neuralyzer.tracked_jobs


async def test_job_id_hex_conversion(make_config, fake_history):
    # "00001A" hex == 26 decimal: a wrong conversion would target job 1A
    # (invalid) or job 20 (octal-style off-by-something), not job 26.
    neuralyzer = load_component(make_config({}))
    calls = []

    async def _fake_delete(job_id, job, rule):
        calls.append(job_id)

    neuralyzer._delete_job = _fake_delete
    await neuralyzer._on_history_changed(
        {"action": "finished", "job": _job("00001A", "cancelled", filament_used=0)}
    )
    assert calls == [26]


async def test_end_to_end_deletes_real_row_through_real_delete_job(make_config, fake_history):
    """Full integration: no mocking of _delete_job, real fake DB."""
    def _seed(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO job_history VALUES (26, 'cancelled')")
        conn.commit()
    await fake_history.history_table.queue_callback(_seed)
    fake_history.job_totals.update({"total_jobs": 1, "total_time": 100.0})

    neuralyzer = load_component(make_config({}))
    await neuralyzer._on_history_changed({
        "action": "finished",
        "job": _job("00001A", "cancelled", filament_used=0, total_duration=100.0, print_duration=80.0),
    })

    assert fake_history.job_totals["total_jobs"] == 0

    def _check(conn):
        return conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone()
    assert await fake_history.history_table.queue_callback(_check) is None


async def test_print_stats_reset_on_cancelled_job_documented_behavior(make_config, fake_history):
    """Documents the spec's accepted tradeoff: a cancelled job whose
    print stats were reset (so filament_used stayed 0 despite real
    printing) is still deleted by the default no_extrusion rule. This
    pins the *current, documented* behavior — it is not a bug fix."""
    neuralyzer = load_component(make_config({}))
    calls = []

    async def _fake_delete(job_id, job, rule):
        calls.append(job_id)

    neuralyzer._delete_job = _fake_delete
    # filament_used == 0 here stands in for "stats were reset", per spec.
    await neuralyzer._on_history_changed({
        "action": "finished",
        "job": _job("000001", "cancelled", filament_used=0),
    })
    assert calls == [1]
