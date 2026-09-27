"""Moonraker component: delete junk print jobs from history.

Deliberately imports nothing from the ``moonraker`` package — every
Moonraker object (config, server, history, template factory) is used
purely by duck typing, matching the shapes those objects have in
Moonraker v0.11.0. This keeps the file a plain, standalone module that
can be symlinked straight into ``moonraker/components/`` and imported
directly (``import neuralyzer``) by tests with no package scaffolding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

RULE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_RULES = ["no_extrusion = {job.filament_used <= 0}"]


def parse_rule_lines(lines: list[str]) -> list[tuple[str, str]]:
    """Parse ``name = {expr}`` lines into ``(name, expr)`` pairs.

    Splits each line at the first ``=`` (an expression may itself
    contain ``==``, since names can't). Rejects duplicate or
    non-``[A-Za-z0-9_]+`` names.
    """
    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        name, _, expr = line.partition("=")
        name = name.strip()
        expr = expr.strip()
        if not RULE_NAME_RE.match(name):
            raise ValueError(f"invalid rule name: {name!r}")
        if name in seen:
            raise ValueError(f"duplicate rule name: {name!r}")
        if not expr:
            raise ValueError(f"malformed rule line, missing '=': {line!r}")
        seen.add(name)
        parsed.append((name, expr))
    return parsed


@dataclass
class Rule:
    name: str
    source: str
    template: Any  # duck-typed: needs .render(context: dict) -> str


def render_rule(rule: Rule, context: dict) -> bool:
    """Render ``rule`` against ``context``; True only on exact "True".

    Any render error (e.g. an ordering comparison against a missing
    value) or non-boolean output is treated as no-match, never as a
    match — a broken rule can only ever keep jobs, never delete them.
    """
    try:
        result = rule.template.render(context)
    except Exception:
        logging.warning(
            "neuralyzer: rule '%s' failed to render, treating as no match",
            rule.name,
            exc_info=True,
        )
        return False
    if result == "True":
        return True
    if result != "False":
        logging.warning(
            "neuralyzer: rule '%s' produced non-boolean output %r, treating as no match",
            rule.name,
            result,
        )
    return False


def first_match(rules: list[Rule], context: dict) -> Rule | None:
    for rule in rules:
        if render_rule(rule, context):
            return rule
    return None


def build_context(job: dict, tracked: bool) -> dict:
    aux: dict[str, dict[str, Any]] = {}
    for entry in job.get("auxiliary_data") or []:
        provider = entry.get("provider")
        name = entry.get("name")
        if provider is None or name is None:
            continue
        aux.setdefault(provider, {})[name] = entry.get("value")

    metadata = job.get("metadata") or {}
    filament_total = metadata.get("filament_total")
    filament_used = job.get("filament_used")
    progress = None
    if filament_total and filament_used is not None:
        progress = filament_used / filament_total

    return {"job": job, "tracked": tracked, "aux": aux, "progress": progress}


BASE_TOTALS = {
    "total_jobs": 0,
    "total_time": 0.0,
    "total_print_time": 0.0,
    "total_filament_used": 0.0,
    "longest_job": 0.0,
    "longest_print": 0.0,
}


def compute_job_delta(job: dict) -> dict:
    return {
        "total_jobs": 1,
        "total_time": job.get("total_duration") or 0,
        "total_print_time": job.get("print_duration") or 0,
        "total_filament_used": job.get("filament_used") or 0,
    }


def apply_job_delta(job_totals: dict, delta: dict) -> dict:
    applied = {}
    for field, amount in delta.items():
        current = job_totals.get(field, 0)
        new_value = current - amount
        if new_value < 0:
            new_value = 0
        applied[field] = current - new_value
        job_totals[field] = new_value
    return applied


def compute_aux_deltas(job: dict, aux_totals: list[dict]) -> list[tuple[int, float]]:
    deltas: list[tuple[int, float]] = []
    for entry in job.get("auxiliary_data") or []:
        provider = entry.get("provider")
        name = entry.get("name")
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for idx, total_entry in enumerate(aux_totals):
            if total_entry.get("provider") == provider and total_entry.get("field") == name:
                if total_entry.get("total") is not None:
                    deltas.append((idx, value))
                break
    return deltas


def apply_aux_deltas(
    aux_totals: list[dict], deltas: list[tuple[int, float]]
) -> list[tuple[int, float]]:
    applied = []
    for idx, amount in deltas:
        entry = aux_totals[idx]
        current = entry["total"]
        new_value = current - amount
        if new_value < 0:
            new_value = 0
        applied.append((idx, current - new_value))
        entry["total"] = new_value
    return applied


class _PreCommitFailure(Exception):
    """The delete/update failed before commit; safely rolled back."""


class _CommitFailure(Exception):
    """The commit itself failed; outcome on disk is unknown."""


_DELETE_SQL = "DELETE FROM job_history WHERE job_id = ?"
_UPDATE_SQL = (
    "UPDATE job_totals SET maximum = :maximum, total = :total "
    "WHERE provider = :provider AND field = :field AND instance_id = :instance_id"
)


def build_update_rows(
    job_fields,
    job_totals: dict,
    aux_totals: list[dict],
    aux_indices: list[int],
    instance: str = "default",
) -> list[dict]:
    rows = [
        {
            "maximum": None,
            "total": job_totals[field],
            "provider": "history",
            "field": field,
            "instance_id": instance,
        }
        for field in job_fields
    ]
    for idx in aux_indices:
        entry = aux_totals[idx]
        rows.append(
            {
                "maximum": entry["maximum"],
                "total": entry["total"],
                "provider": entry["provider"],
                "field": entry["field"],
                "instance_id": instance,
            }
        )
    return rows


def delete_and_correct(conn, job_id: int, rows: list[dict]) -> None:
    """Delete a job and correct totals atomically, on the DB thread.

    Runs synchronously inside a single ``queue_callback`` so the
    SAVEPOINT, the DELETE, and the UPDATEs are one item on Moonraker's
    FIFO SQLite command queue — never interleaved with another
    writer's statements. ``conn`` is duck-typed (any object exposing
    ``execute``/``commit`` like ``sqlite3.Connection``) so tests can
    substitute a thin proxy to force a commit failure.

    Only the DELETE's row count is checked. A totals UPDATE matching
    zero rows is not an error: it happens, by design, during the brief
    window of a totals reset (History's own DELETE + INSERT on
    job_totals are two separate queue items), and the reset's INSERT
    is the correct outcome in that case.

    The SAVEPOINT statement and the success-path RELEASE both live
    *inside* the same try/except as the DELETE and UPDATEs — not just
    after it — so that even a failure opening or releasing the
    savepoint itself is classified as `_PreCommitFailure` rather than
    propagating raw and leaving the caller's already-applied in-memory
    totals delta un-reverted.

    If the recovery `ROLLBACK TO`/`RELEASE` itself fails, the outcome
    is genuinely unknown — the DELETE/UPDATEs may or may not have
    landed — so that case is deliberately raised as `_CommitFailure`,
    not `_PreCommitFailure`: `_delete_job` treats `_CommitFailure` as
    "log and do not compensate," which is the only safe response when
    we can no longer prove nothing was persisted. Treating a failed
    rollback as safe-to-compensate would risk re-adding a totals delta
    on top of a DELETE that actually committed.

    A failure of the `SAVEPOINT` statement itself is a separate case:
    if the savepoint never opened, no statement after it had a chance
    to run, so there is nothing to roll back and the failure is always
    safe to compensate — `savepoint_opened` tracks this so that case
    goes straight to `_PreCommitFailure` without attempting (and
    necessarily failing) a `ROLLBACK TO` a savepoint that was never
    opened.
    """
    savepoint_opened = False
    try:
        conn.execute("SAVEPOINT neuralyzer")
        savepoint_opened = True
        cursor = conn.execute(_DELETE_SQL, (job_id,))
        if cursor.rowcount == 0:
            raise RuntimeError(f"no job_history row for job_id {job_id}")
        for row in rows:
            conn.execute(_UPDATE_SQL, row)
        conn.execute("RELEASE neuralyzer")
    except Exception as exc:
        if not savepoint_opened:
            raise _PreCommitFailure(str(exc)) from exc
        try:
            conn.execute("ROLLBACK TO neuralyzer")
            conn.execute("RELEASE neuralyzer")
        except Exception as rollback_exc:
            raise _CommitFailure(str(rollback_exc)) from exc
        raise _PreCommitFailure(str(exc)) from exc
    try:
        conn.commit()
    except Exception as exc:
        raise _CommitFailure(str(exc)) from exc


class Neuralyzer:
    def __init__(self, config) -> None:
        self.server = config.get_server()
        self.dry_run = config.getboolean("dry_run", False)
        self.process_completed = config.getboolean("process_completed", False)

        raw_lines = config.getlist("rules", DEFAULT_RULES)
        try:
            rule_defs = parse_rule_lines(raw_lines)
        except ValueError as exc:
            raise config.error(str(exc)) from exc

        template_factory = self.server.lookup_component("template")
        self.rules = [
            Rule(name=name, source=expr, template=template_factory.create_template(expr))
            for name, expr in rule_defs
        ]

        self.history = self.server.lookup_component("history")
        self.tracked_jobs: set[int] = set()
        self.server.register_event_handler("history:history_changed", self._on_history_changed)
        self.server.register_remote_method("neuralyzer_start_tracking", self._on_start_tracking)

    async def _delete_job(self, job_id: int, job: dict, rule: Rule) -> None:
        job_totals_ref = self.history.job_totals

        job_delta = compute_job_delta(job)
        applied_job_delta = apply_job_delta(self.history.job_totals, job_delta)
        aux_deltas = compute_aux_deltas(job, self.history.aux_totals)
        applied_aux_deltas = apply_aux_deltas(self.history.aux_totals, aux_deltas)

        rows = build_update_rows(
            applied_job_delta.keys(),
            self.history.job_totals,
            self.history.aux_totals,
            [idx for idx, _ in applied_aux_deltas],
        )

        def _run(conn):
            delete_and_correct(conn, job_id, rows)

        try:
            await self.history.history_table.queue_callback(_run)
        except _PreCommitFailure:
            # job_totals (the dict) is only ever replaced wholesale by a
            # totals reset -- a normal finish_job() mutates it in place.
            # aux_totals (the list), by contrast, is rebuilt as a new
            # list object on *every* finish, so its identity can't be
            # used as a reset signal: a reset is detected purely via
            # job_totals, and the aux delta is re-added into whichever
            # aux_totals list is current, at the index captured before
            # the await (stable, since History rebuilds it in the same
            # fixed field order every time).
            reset_happened = self.history.job_totals is not job_totals_ref
            revert_job_fields: list[str] = []
            revert_aux_indices: list[int] = []
            if not reset_happened:
                for field, amount in applied_job_delta.items():
                    self.history.job_totals[field] += amount
                revert_job_fields = list(applied_job_delta.keys())
                for idx, amount in applied_aux_deltas:
                    self.history.aux_totals[idx]["total"] += amount
                revert_aux_indices = [idx for idx, _ in applied_aux_deltas]

            revert_rows = build_update_rows(
                revert_job_fields,
                self.history.job_totals,
                self.history.aux_totals,
                revert_aux_indices,
            )

            def _persist_revert(conn):
                for row in revert_rows:
                    conn.execute(_UPDATE_SQL, row)
                conn.commit()

            try:
                if revert_rows:
                    await self.history.history_table.queue_callback(_persist_revert)
            except Exception:
                logging.error(
                    "neuralyzer: failed to persist reverted totals for job %s",
                    job.get("job_id"),
                    exc_info=True,
                )
            logging.warning(
                "neuralyzer: failed to delete job %s, keeping it in history",
                job.get("job_id"),
            )
        except _CommitFailure:
            logging.error(
                "neuralyzer: commit failed while deleting job %s, totals may be inconsistent",
                job.get("job_id"),
                exc_info=True,
            )
        else:
            logging.info(
                "neuralyzer: deleted job %s (%s), rule '%s'",
                job.get("job_id"),
                job.get("filename"),
                rule.name,
            )

    def _on_start_tracking(self, **kwargs) -> None:
        job_id = self.history.current_job_id
        if job_id is None:
            return
        self.tracked_jobs.add(job_id)

    async def _on_history_changed(self, event_data: dict) -> None:
        if event_data.get("action") != "finished":
            return
        job = event_data["job"]
        job_id = int(job["job_id"], 16)
        tracked = job_id in self.tracked_jobs
        self.tracked_jobs.discard(job_id)

        if job.get("status") == "completed" and not self.process_completed:
            return

        context = build_context(job, tracked)
        rule = first_match(self.rules, context)
        if rule is None:
            return

        if self.dry_run:
            logging.info(
                "neuralyzer: would delete job %s (%s), rule '%s'",
                job.get("job_id"),
                job.get("filename"),
                rule.name,
            )
            return

        await self._delete_job(job_id, job, rule)


def load_component(config) -> Neuralyzer:
    return Neuralyzer(config)
