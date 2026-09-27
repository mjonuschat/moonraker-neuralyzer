import jinja2
import pytest

from neuralyzer import Rule, first_match, render_rule


def _rule(name: str, source: str) -> Rule:
    env = jinja2.Environment("{%", "%}", "{", "}")
    template = env.from_string(source)

    class _Template:
        def render(self, context):
            return template.render(context).strip()

    return Rule(name=name, source=source, template=_Template())


def test_exact_true_matches():
    rule = _rule("r", "{job.filament_used <= 0}")
    assert render_rule(rule, {"job": {"filament_used": 0}}) is True


def test_exact_false_does_not_match():
    rule = _rule("r", "{job.filament_used <= 0}")
    assert render_rule(rule, {"job": {"filament_used": 5}}) is False


@pytest.mark.parametrize("source", ["{job.filament_used}", "{None}", "{1}"])
def test_non_boolean_output_is_no_match_and_warns(source, caplog):
    rule = _rule("r", source)
    with caplog.at_level("WARNING"):
        result = render_rule(rule, {"job": {"filament_used": 5}})
    assert result is False
    assert "r" in caplog.text


def test_render_error_is_no_match_and_warns(caplog):
    # Ordering comparison against a missing value raises in Jinja's
    # default (non-strict) Undefined.
    rule = _rule("r", "{progress < 0.02}")
    with caplog.at_level("WARNING"):
        result = render_rule(rule, {"progress": None})
    assert result is False
    assert "r" in caplog.text


def test_misspelled_equality_quietly_false_forever():
    # Documents a spec-called-out gotcha: no warning, just False.
    rule = _rule("r", '{job.statsu == "cancelled"}')
    assert render_rule(rule, {"job": {"status": "cancelled"}}) is False


def test_first_match_stops_at_first_matching_rule():
    rules = [
        _rule("a", "{False}"),
        _rule("b", "{True}"),
        _rule("c", "{True}"),
    ]
    assert first_match(rules, {}).name == "b"


def test_first_match_none_when_nothing_matches():
    rules = [_rule("a", "{False}")]
    assert first_match(rules, {}) is None


def test_broken_rule_never_blocks_a_later_match():
    rules = [_rule("broken", "{progress < 0.02}"), _rule("ok", "{True}")]
    assert first_match(rules, {"progress": None}).name == "ok"
