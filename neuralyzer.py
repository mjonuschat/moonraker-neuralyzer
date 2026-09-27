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
            rule.name, exc_info=True,
        )
        return False
    if result == "True":
        return True
    if result != "False":
        logging.warning(
            "neuralyzer: rule '%s' produced non-boolean output %r, "
            "treating as no match", rule.name, result,
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
