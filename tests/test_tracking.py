import asyncio

from neuralyzer import load_component


async def test_ignores_signal_when_no_current_job(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking()
    assert neuralyzer.tracked_jobs == set()


async def test_tracks_current_job_id(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = 5
    neuralyzer._on_start_tracking()
    assert neuralyzer.tracked_jobs == {5}


async def test_remote_method_registered_and_dispatches(make_config, fake_history, fake_server):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = 7
    fake_server.call_remote_method("neuralyzer_start_tracking")
    assert 7 in neuralyzer.tracked_jobs


async def test_interleaved_finished_and_added_evaluate_own_tracking_state(
    make_config, fake_history
):
    """Job A (untracked) finishes and matches `untracked`, so its real
    delete path runs a genuine DB round trip. While A's delete callback
    is paused mid-flight *in the database layer* (the exact point the
    spec describes: queued and awaiting the shared SQLite connection),
    Job B's NEURALYZER_START signal must be recorded for B, not lost or
    attributed to A. B then finishes tracked, so it must be kept,
    proving each job's own tracking state was used, not a shared flag.
    Nothing here is mocked: both jobs' real rows are seeded and both
    go through the unmodified `_on_history_changed`/`_delete_job`."""

    def _seed_a(conn):
        conn.execute("INSERT INTO job_history VALUES (1, 'cancelled')")
        conn.commit()

    await fake_history.history_table.queue_callback(_seed_a)

    neuralyzer = load_component(make_config({"rules": "untracked = {not tracked}"}))

    fake_history.pause()
    task_a = asyncio.create_task(
        neuralyzer._on_history_changed(
            {
                "action": "finished",
                "job": {"job_id": "000001", "status": "cancelled", "filament_used": 5},
            }
        )
    )
    await asyncio.sleep(0)  # let A's synchronous prep run and queue its DB callback

    # B starts while A's delete is genuinely paused in the database layer.
    fake_history.current_job_id = 2
    neuralyzer._on_start_tracking()
    assert neuralyzer.tracked_jobs == {2}  # B's signal recorded, not lost or misfiled

    fake_history.resume()
    await task_a

    def _check_a(conn):
        return conn.execute("SELECT * FROM job_history WHERE job_id = 1").fetchone()

    assert await fake_history.history_table.queue_callback(_check_a) is None  # A deleted

    # B finishes tracked -> untracked is False -> B is kept, not deleted.
    def _seed_b(conn):
        conn.execute("INSERT INTO job_history VALUES (2, 'cancelled')")
        conn.commit()

    await fake_history.history_table.queue_callback(_seed_b)

    await neuralyzer._on_history_changed(
        {
            "action": "finished",
            "job": {"job_id": "000002", "status": "cancelled", "filament_used": 5},
        }
    )

    def _check_b(conn):
        return conn.execute("SELECT * FROM job_history WHERE job_id = 2").fetchone()

    assert await fake_history.history_table.queue_callback(_check_b) is not None  # B kept
    assert neuralyzer.tracked_jobs == set()


async def test_signal_before_job_id_exists_is_applied_when_job_added(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking()

    fake_history.current_job_id = 10
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000A"}})

    assert neuralyzer.tracked_jobs == {10}


async def test_early_signal_applies_to_one_job_only(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking()

    fake_history.current_job_id = 10
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000A"}})
    fake_history.current_job_id = 11
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000B"}})

    assert neuralyzer.tracked_jobs == {10}


async def test_early_signal_survives_a_slow_job_save(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking(state="printing")

    await asyncio.sleep(0.05)
    fake_history.current_job_id = 10
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000A"}})

    assert neuralyzer.tracked_jobs == {10}


async def test_signal_outside_printing_state_is_ignored(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking(state="standby")

    fake_history.current_job_id = 10
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000A"}})

    assert neuralyzer.tracked_jobs == set()


async def test_signal_outside_printing_state_ignored_even_with_current_job(
    make_config, fake_history
):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = 5
    neuralyzer._on_start_tracking(state="complete")

    assert neuralyzer.tracked_jobs == set()


async def test_printing_state_tracks_current_job(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = 5
    neuralyzer._on_start_tracking(state="printing")

    assert neuralyzer.tracked_jobs == {5}


async def test_stale_added_event_does_not_consume_early_signal(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    fake_history.current_job_id = None
    neuralyzer._on_start_tracking(state="printing")

    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "000009"}})
    assert neuralyzer.tracked_jobs == set()

    fake_history.current_job_id = 10
    await neuralyzer._on_history_changed({"action": "added", "job": {"job_id": "00000A"}})

    assert neuralyzer.tracked_jobs == {10}
