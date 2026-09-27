"""Moonraker component: delete junk print jobs from history.

Deliberately imports nothing from the ``moonraker`` package — every
Moonraker object (config, server, history, template factory) is used
purely by duck typing, matching the shapes those objects have in
Moonraker v0.11.0. This keeps the file a plain, standalone module that
can be symlinked straight into ``moonraker/components/`` and imported
directly (``import neuralyzer``) by tests with no package scaffolding.
"""
from __future__ import annotations

import re

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
