import sqlite3

import pytest

from neuralyzer import (
    _CommitFailure,
    _PreCommitFailure,
    build_update_rows,
    delete_and_correct,
)

# A deliberately minimal projection of Moonraker's real schema (see
# HistorySqlDefinition / TotalsSqlDefinition in moonraker/components/history.py)
# — just enough columns to exercise the DELETE + UPDATE logic in isolation.
SCHEMA = """
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


class _FailingCommitConn:
    """Wraps a real connection but fails on commit.

    `sqlite3.Connection.commit` is a read-only slot on the real type,
    so it can't be monkeypatched directly — this thin duck-typed proxy
    forwards `execute` to the real connection and fails only `commit`,
    to exercise delete_and_correct's post-RELEASE failure path.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self) -> None:
        raise sqlite3.OperationalError("disk full")


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "moonraker-test.db"
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA)
    c.execute("INSERT INTO job_history VALUES (26, 'cancelled')")
    c.executemany(
        "INSERT INTO job_totals VALUES (?, ?, ?, ?, ?)",
        [
            ("history", "total_jobs", None, 5, "default"),
            ("history", "total_time", None, 500.0, "default"),
        ],
    )
    c.commit()
    yield c
    c.close()


def test_build_update_rows_shapes_dicts():
    job_totals = {"total_jobs": 4, "total_time": 380.0}
    rows = build_update_rows(["total_jobs", "total_time"], job_totals, [], [])
    assert rows == [
        {
            "maximum": None,
            "total": 4,
            "provider": "history",
            "field": "total_jobs",
            "instance_id": "default",
        },
        {
            "maximum": None,
            "total": 380.0,
            "provider": "history",
            "field": "total_time",
            "instance_id": "default",
        },
    ]


def test_build_update_rows_includes_aux_entries():
    aux_totals = [{"provider": "power_meter", "field": "energy", "maximum": None, "total": 46.8}]
    rows = build_update_rows([], {}, aux_totals, [0])
    assert rows == [
        {
            "maximum": None,
            "total": 46.8,
            "provider": "power_meter",
            "field": "energy",
            "instance_id": "default",
        },
    ]


def test_happy_path_deletes_and_updates(conn):
    rows = build_update_rows(
        ["total_jobs", "total_time"], {"total_jobs": 4, "total_time": 380.0}, [], []
    )
    delete_and_correct(conn, 26, rows)
    assert conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone() is None
    row = conn.execute("SELECT total FROM job_totals WHERE field = 'total_jobs'").fetchone()
    assert row[0] == 4


def test_missing_row_raises_precommit_failure_and_leaves_totals(conn):
    with pytest.raises(_PreCommitFailure):
        delete_and_correct(conn, 999999, [])
    # totals untouched, and the (still-present) job_history row untouched
    row = conn.execute("SELECT total FROM job_totals WHERE field = 'total_jobs'").fetchone()
    assert row[0] == 5
    assert conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone() is not None


def test_rollback_to_savepoint_preserves_other_pending_writer(conn):
    # Simulate another writer's uncommitted statement on the same
    # connection, pending when our callback runs.
    conn.execute("INSERT INTO job_history VALUES (27, 'in_progress')")
    with pytest.raises(_PreCommitFailure):
        delete_and_correct(conn, 999999, [])
    # Our ROLLBACK TO SAVEPOINT must not have discarded the pending insert.
    assert conn.execute("SELECT * FROM job_history WHERE job_id = 27").fetchone() is not None


def test_commit_success_also_commits_other_writers_pending_sql(conn):
    conn.execute("INSERT INTO job_history VALUES (27, 'in_progress')")
    rows = build_update_rows(["total_jobs"], {"total_jobs": 4}, [], [])
    delete_and_correct(conn, 26, rows)
    fresh = sqlite3.connect(conn.execute("PRAGMA database_list").fetchone()[2])
    assert fresh.execute("SELECT * FROM job_history WHERE job_id = 27").fetchone() is not None
    fresh.close()


def test_commit_failure_raises_distinct_exception(conn):
    rows = build_update_rows(["total_jobs"], {"total_jobs": 4}, [], [])
    with pytest.raises(_CommitFailure):
        delete_and_correct(_FailingCommitConn(conn), 26, rows)
    # The point of this test is that the distinct _CommitFailure type
    # propagated, not _PreCommitFailure or a raw sqlite3 error — the
    # real connection's own transaction state is irrelevant here since
    # we never called commit() on it directly.


class _FailingSavepointConn:
    """Fails only the initial SAVEPOINT statement.

    Proves that a failure before any real statement runs is still
    classified as `_PreCommitFailure`, not raised raw — the SAVEPOINT
    call itself must be inside the same try/except as everything else.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("SAVEPOINT"):
            raise sqlite3.OperationalError("cannot open savepoint")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        self._real.commit()


def test_savepoint_failure_is_classified_as_precommit_failure(conn):
    with pytest.raises(_PreCommitFailure):
        delete_and_correct(_FailingSavepointConn(conn), 26, [])


class _FailingRollbackConn:
    """Fails `ROLLBACK TO` after a real pre-commit failure.

    Proves that when the recovery rollback itself fails — so we can no
    longer prove the DELETE/UPDATEs didn't land — the outcome is
    raised as `_CommitFailure` (indeterminate, no compensation), never
    as the safe-to-compensate `_PreCommitFailure`.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql, *args, **kwargs):
        if sql.strip().upper().startswith("ROLLBACK TO"):
            raise sqlite3.OperationalError("cannot rollback")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        self._real.commit()


def test_rollback_failure_is_classified_as_commit_failure(conn):
    # job_id 999999 doesn't exist -> triggers the pre-commit failure
    # path, whose ROLLBACK TO then fails too.
    with pytest.raises(_CommitFailure):
        delete_and_correct(_FailingRollbackConn(conn), 999999, [])


def test_update_matching_zero_rows_does_not_fail_the_delete(conn):
    # Simulates the documented reset race: History's own DELETE (of
    # the totals rows, mid-reset) has already run and committed by the
    # time our UPDATE executes, so it matches nothing. History's own
    # INSERT will supply the authoritative (zeroed) values afterward,
    # and that is the correct outcome (spec's Error Handling section:
    # "the reset wins, which is the correct outcome") — so a totals
    # UPDATE matching zero rows must never fail the whole delete.
    conn.execute("DELETE FROM job_totals")
    conn.commit()
    rows = build_update_rows(["total_jobs"], {"total_jobs": 4}, [], [])
    delete_and_correct(conn, 26, rows)  # must not raise
    assert conn.execute("SELECT * FROM job_history WHERE job_id = 26").fetchone() is None
    assert conn.execute("SELECT * FROM job_totals").fetchall() == []
