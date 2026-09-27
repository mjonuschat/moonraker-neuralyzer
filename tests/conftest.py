import asyncio
import queue
import sqlite3
import threading

import jinja2
import pytest

from neuralyzer import BASE_TOTALS

# Mirrors the minimal schema used in tests/test_delete_and_correct.py.
_SCHEMA = """
CREATE TABLE job_history (
    job_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL
);
CREATE TABLE job_totals (
    provider TEXT NOT NULL,
    field TEXT NOT NULL,
    maximum REAL,
    total REAL,
    instance_id TEXT NOT NULL,
    PRIMARY KEY (provider, field, instance_id)
);
"""

_BOOLEAN_STATES = {
    "1": True,
    "yes": True,
    "true": True,
    "on": True,
    "0": False,
    "no": False,
    "false": False,
    "off": False,
}


class ConfigError(Exception):
    pass


class FakeConfig:
    """Duck-typed stand-in for moonraker.confighelper.ConfigHelper."""

    error = ConfigError

    def __init__(self, options: dict, server: "FakeServer") -> None:
        self._options = options
        self._server = server

    def get_server(self) -> "FakeServer":
        return self._server

    def getboolean(self, option: str, default: bool = False) -> bool:
        raw = self._options.get(option)
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        value = str(raw).strip().lower()
        if value not in _BOOLEAN_STATES:
            raise ValueError(f"invalid boolean value for {option!r}: {raw!r}")
        return _BOOLEAN_STATES[value]

    def getlist(self, option: str, default=None) -> list[str]:
        raw = self._options.get(option)
        if raw is None:
            return list(default) if default is not None else []
        return [line.strip() for line in raw.split("\n") if line.strip()]


class FakeServer:
    """Duck-typed stand-in for moonraker.server.Server."""

    def __init__(self) -> None:
        self._components: dict[str, object] = {}
        self._events: dict[str, list] = {}
        self._remote_methods: dict[str, object] = {}

    def register_component(self, name: str, component: object) -> None:
        self._components[name] = component

    def lookup_component(self, name: str):
        return self._components[name]

    def register_event_handler(self, event: str, callback) -> None:
        self._events.setdefault(event, []).append(callback)

    def register_remote_method(self, name: str, callback) -> None:
        self._remote_methods[name] = callback

    def call_remote_method(self, name: str, **kwargs) -> None:
        self._remote_methods[name](**kwargs)


class _FakeTemplate:
    def __init__(self, template: jinja2.Template) -> None:
        self._template = template

    def render(self, context: dict) -> str:
        return self._template.render(context).strip()


class FakeTemplateFactory:
    """Duck-typed stand-in for moonraker.components.template.TemplateFactory."""

    def __init__(self) -> None:
        self._env = jinja2.Environment("{%", "%}", "{", "}")

    def create_template(self, source: str) -> _FakeTemplate:
        return _FakeTemplate(self._env.from_string(source))


class _FakeSqliteQueue:
    """Mirrors moonraker.components.database.SqliteProvider's FIFO thread.

    ``pause``/``resume`` gate a ``threading.Event`` the worker checks
    before running each dequeued callback, so a test can deterministically
    freeze a callback in flight, mutate shared state from the event loop
    thread, then release it — instead of guessing at scheduling via sleeps.

    Each call to ``queue_callback`` binds its future to whichever event
    loop is *running at that moment* (``asyncio.get_running_loop()``),
    not a loop captured once at construction time. pytest-asyncio can
    run different tests (and even fixture setup) against different
    event loop instances, so capturing "the" loop up front risks a
    "Future attached to a different loop" error; binding per-call is
    correct regardless of how fixtures and tests are scheduled.
    """

    def __init__(self, db_path: str) -> None:
        self._queue: "queue.Queue" = queue.Queue()
        self._gate = threading.Event()
        self._gate.set()
        self._thread = threading.Thread(target=self._run, args=(db_path,), daemon=True)
        self._thread.start()

    def _run(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        while True:
            fut, func, loop = self._queue.get()
            if func is None:
                break
            self._gate.wait()
            try:
                result = func(conn)
            except Exception as exc:
                loop.call_soon_threadsafe(fut.set_exception, exc)
            else:
                loop.call_soon_threadsafe(fut.set_result, result)
        conn.close()

    def queue_callback(self, callback):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._queue.put((fut, callback, loop))
        return fut

    def pause(self) -> None:
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def stop(self) -> None:
        self._gate.set()
        self._queue.put((None, None, None))
        self._thread.join()


class _FakeHistoryTable:
    def __init__(self, sqlite_queue: _FakeSqliteQueue) -> None:
        self._sqlite_queue = sqlite_queue

    def queue_callback(self, callback):
        return self._sqlite_queue.queue_callback(callback)


class FakeHistory:
    """Duck-typed stand-in for moonraker.components.history.History."""

    def __init__(self, db_path: str) -> None:
        self._sqlite_queue = _FakeSqliteQueue(db_path)
        self.history_table = _FakeHistoryTable(self._sqlite_queue)
        self.job_totals = dict(BASE_TOTALS)
        self.aux_totals: list[dict] = []
        self.current_job_id = None

    def pause(self) -> None:
        self._sqlite_queue.pause()

    def resume(self) -> None:
        self._sqlite_queue.resume()

    def stop(self) -> None:
        self._sqlite_queue.stop()


@pytest.fixture
def fake_server() -> FakeServer:
    server = FakeServer()
    server.register_component("template", FakeTemplateFactory())
    return server


@pytest.fixture
def fake_history(tmp_path, fake_server):
    db_path = str(tmp_path / "moonraker-test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    # Mirrors a real Moonraker DB's invariant: by the time any job has
    # ever finished, all six job_totals rows already exist (finish_job's
    # _update_job_totals() REPLACEs all of them on every call). Seeding
    # them here means UPDATE-based tests exercise a real row match
    # instead of a silent zero-row no-op.
    conn.executemany(
        "INSERT INTO job_totals VALUES (?, ?, ?, ?, ?)",
        [
            ("history", "total_jobs", None, 0, "default"),
            ("history", "total_time", None, 0.0, "default"),
            ("history", "total_print_time", None, 0.0, "default"),
            ("history", "total_filament_used", None, 0.0, "default"),
            ("history", "longest_job", 0.0, None, "default"),
            ("history", "longest_print", 0.0, None, "default"),
        ],
    )
    conn.commit()
    conn.close()
    history = FakeHistory(db_path)
    fake_server.register_component("history", history)
    yield history
    history.stop()


@pytest.fixture
def make_config(fake_server):
    def _make(options: dict) -> FakeConfig:
        return FakeConfig(options, fake_server)

    return _make
