import pytest

from neuralyzer import load_component


def test_loads_with_default_config(make_config, fake_history):
    config = make_config({})
    neuralyzer = load_component(config)
    assert neuralyzer.dry_run is False
    assert neuralyzer.process_completed is False
    assert [r.name for r in neuralyzer.rules] == ["no_extrusion"]


def test_reads_dry_run_and_process_completed(make_config, fake_history):
    config = make_config({"dry_run": "true", "process_completed": "true"})
    neuralyzer = load_component(config)
    assert neuralyzer.dry_run is True
    assert neuralyzer.process_completed is True


def test_custom_rules_replace_default(make_config, fake_history):
    config = make_config(
        {
            "rules": "barely_started = {progress < 0.02}\nuntracked = {not tracked}",
        }
    )
    neuralyzer = load_component(config)
    assert [r.name for r in neuralyzer.rules] == ["barely_started", "untracked"]


def test_empty_rules_option_means_no_rules(make_config, fake_history):
    config = make_config({"rules": ""})
    neuralyzer = load_component(config)
    assert neuralyzer.rules == []


def test_history_and_tracked_jobs_initialized(make_config, fake_history):
    neuralyzer = load_component(make_config({}))
    assert neuralyzer.history is fake_history
    assert neuralyzer.tracked_jobs == set()


def test_invalid_rule_name_raises_at_load(make_config, fake_history):
    config = make_config({"rules": "bad name = {True}"})
    with pytest.raises(Exception):
        load_component(config)


def test_jinja_syntax_error_raises_at_load(make_config, fake_history):
    config = make_config({"rules": "broken = {job.filament_used <=}"})
    with pytest.raises(Exception):
        load_component(config)
