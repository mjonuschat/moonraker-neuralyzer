import pytest

from neuralyzer import DEFAULT_RULES, parse_rule_lines


def test_parses_single_rule():
    parsed = parse_rule_lines(["no_extrusion = {job.filament_used <= 0}"])
    assert parsed == [("no_extrusion", "{job.filament_used <= 0}")]


def test_equals_inside_expression_is_kept():
    parsed = parse_rule_lines(['untracked = {job.status == "cancelled"}'])
    assert parsed == [("untracked", '{job.status == "cancelled"}')]


def test_duplicate_names_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        parse_rule_lines(
            [
                "no_extrusion = {job.filament_used <= 0}",
                "no_extrusion = {job.filament_used < 1}",
            ]
        )


def test_invalid_name_rejected():
    with pytest.raises(ValueError, match="invalid rule name"):
        parse_rule_lines(["bad name = {job.filament_used <= 0}"])


def test_default_rules_parse_cleanly():
    parsed = parse_rule_lines(DEFAULT_RULES)
    assert parsed == [("no_extrusion", "{job.filament_used <= 0}")]


def test_empty_list_gives_no_rules():
    assert parse_rule_lines([]) == []


def test_missing_equals_rejected():
    with pytest.raises(ValueError, match="malformed"):
        parse_rule_lines(["bareword"])
