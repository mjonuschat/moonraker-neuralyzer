from pathlib import Path

from neuralyzer import DEFAULT_RULES, parse_rule_lines


def _readme_neuralyzer_config_block() -> list[str]:
    lines = Path("README.md").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "[neuralyzer]")
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "```")
    return lines[start:end]


def test_readme_config_rules_block_parses():
    config_lines = _readme_neuralyzer_config_block()
    # Extract the `rules:` block the same way config.getlist would see
    # it: everything after `rules:` up to the next top-level option.
    start = next(i for i, line in enumerate(config_lines) if line.strip() == "rules:")
    rule_lines = []
    for line in config_lines[start + 1 :]:
        if line.startswith("[") or (line and not line[0].isspace()):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rule_lines.append(stripped)
    parsed = parse_rule_lines(rule_lines)
    names = [name for name, _ in parsed]
    # `untracked` ships commented out in the README: it requires
    # NEURALYZER_START to be wired up, or it deletes every untracked print.
    assert names == ["no_extrusion", "barely_started"]


def test_default_rules_still_match_documented_default():
    assert DEFAULT_RULES == ["no_extrusion = {job.filament_used <= 0}"]
