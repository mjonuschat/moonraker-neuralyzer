import asyncio

import pytest


async def test_history_table_queue_callback_is_fifo(fake_history):
    order = []

    def first(conn):
        import time
        time.sleep(0.05)
        order.append("first")

    def second(conn):
        order.append("second")

    fut1 = fake_history.history_table.queue_callback(first)
    fut2 = fake_history.history_table.queue_callback(second)
    await asyncio.gather(fut1, fut2)
    assert order == ["first", "second"]


async def test_pause_blocks_queued_callbacks_until_resume(fake_history):
    ran = []
    fake_history.pause()
    fut = fake_history.history_table.queue_callback(lambda conn: ran.append("x"))
    await asyncio.sleep(0.05)
    assert ran == []  # still blocked on the gate
    fake_history.resume()
    await fut
    assert ran == ["x"]


def test_fake_config_getlist_splits_and_strips(make_config):
    config = make_config({"rules": "a = {True}\n  b = {False}  \n\n"})
    assert config.getlist("rules") == ["a = {True}", "b = {False}"]


def test_fake_config_getlist_missing_option_returns_default(make_config):
    config = make_config({})
    assert config.getlist("rules", ["fallback"]) == ["fallback"]


def test_fake_config_getboolean_parses_true_and_false_strings(make_config):
    config = make_config({"dry_run": "false", "process_completed": "true"})
    assert config.getboolean("dry_run") is False
    assert config.getboolean("process_completed") is True


def test_fake_config_getboolean_default_when_missing(make_config):
    config = make_config({})
    assert config.getboolean("dry_run", False) is False
    assert config.getboolean("dry_run", True) is True


def test_fake_template_factory_renders_boolean_strings(fake_server):
    factory = fake_server.lookup_component("template")
    template = factory.create_template("{job.filament_used <= 0}")
    assert template.render({"job": {"filament_used": 0}}) == "True"
    assert template.render({"job": {"filament_used": 1}}) == "False"


def test_fake_history_seeded_with_base_totals(fake_history):
    from neuralyzer import BASE_TOTALS
    assert fake_history.job_totals == BASE_TOTALS
    assert fake_history.aux_totals == []
    assert fake_history.current_job_id is None


async def test_fake_history_db_seeded_with_base_totals_rows(fake_history):
    def _read(conn):
        return dict(
            (field, total)
            for field, total in conn.execute(
                "SELECT field, total FROM job_totals WHERE provider = 'history'"
            ).fetchall()
        )
    rows = await fake_history.history_table.queue_callback(_read)
    assert rows == {
        "total_jobs": 0, "total_time": 0.0,
        "total_print_time": 0.0, "total_filament_used": 0.0,
    }
