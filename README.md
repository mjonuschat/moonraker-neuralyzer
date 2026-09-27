# moonraker-neuralyzer

A Moonraker component that removes junk print jobs from history so they don't pollute stats and filament tracking.

## What it does

neuralyzer watches for finished print jobs and deletes the ones that never really printed anything. It also corrects the running totals it deletes from, so a removed job doesn't leave your statistics half-updated. You control which jobs qualify through a small set of rules in `moonraker.conf`; the default rule catches the most common case (nothing was extruded) without any configuration at all.

Without it, Moonraker records every print job to history, whether or not the print was real. A failed bed probe, an emergency stop, or a slicer start-gcode error all leave a row behind, right alongside your legitimate prints. Over time these rows skew your total filament used, your print time, and your per-file success counts, and they clutter the history views in Mainsail and Fluidd.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/mjonuschat/moonraker-neuralyzer/main/install.sh | bash -s -- ~/moonraker
```

Next, add a `[neuralyzer]` section to `moonraker.conf`; see Configuration below for the options and a starting config.

Optionally, add this macro to `printer.cfg` and call `NEURALYZER_START` from your slicer's start gcode or as the last line of your `PRINT_START` macro:

```ini
[gcode_macro NEURALYZER_START]
description: Signal to moonraker-neuralyzer that the real print has started
gcode:
    {action_call_remote_method("neuralyzer_start_tracking")}
```

This lets rules ask whether a print ever reached that point through the `tracked` variable. Without it, `tracked` is always false, so a rule like `untracked` below would delete every cancelled or errored print, including real ones.

## Configuration

```ini
[neuralyzer]
# Log what would be deleted, without touching history or totals.
# Default: false. Start with this set to true and watch the logs for
# "would delete" lines that look right before turning it off.
dry_run: true

# Whether rules also apply to jobs that finished with status "completed".
# Default: false (completed jobs are always kept)
process_completed: false

# Named rules, one per line: name = {expression}
# The first matching rule deletes the job; evaluation then stops.
# Default: the no_extrusion rule alone.
rules:
  # Nothing was extruded at all
  no_extrusion = {job.filament_used <= 0}

  # Cancelled or failed before a meaningful part of the file was printed
  barely_started = {job.status != "completed" and progress < 0.02}

  # Start gcode never reached NEURALYZER_START (probing, heating, purge
  # failed). Requires the macro from Installation above; without it,
  # `tracked` is always false and this rule deletes every cancelled or
  # errored print.
  # untracked = {not tracked}
```

neuralyzer never deletes a print that finished with status `completed` unless you set `process_completed: true`. A print that ran to completion is a real print by definition.

## Rules

Each line under `rules` has the form `name = {expression}`. The name identifies the rule in log messages and must be unique. The expression is a Jinja2 template using Moonraker's single-brace syntax, the same syntax Klipper gcode macros use.

neuralyzer evaluates rules in order. The first one whose expression renders to exactly the string `True` deletes the job, and evaluation stops there. If no rule matches, the job stays in history. There are no rules that keep a job; the `completed` guard above is the only exception to deletion.

If you omit `rules` entirely, neuralyzer falls back to the single default rule (`no_extrusion`). If you supply `rules`, it replaces the default set completely; an empty `rules:` means nothing is ever deleted.

If a rule fails to render, for example because it compares against a value that doesn't exist, neuralyzer treats it as a non-match and deletes nothing. A broken rule can only keep a job that should have been deleted; it can never delete one that should have been kept.

### Available variables

| Variable | Contents |
|---|---|
| `job` | The finished job, in the same shape the `/server/history/job` endpoint returns: `job_id`, `status`, `filename`, `filament_used`, `print_duration`, `total_duration`, `start_time`, `end_time`, `user`, `exists`, `metadata`, `auxiliary_data`. |
| `tracked` | `true` if `NEURALYZER_START` was called during this job. |
| `aux` | `job.auxiliary_data` reshaped as `aux[provider][name]`, for example `aux.power_meter.energy`. |
| `progress` | `job.filament_used` divided by `job.metadata.filament_total`, or `none` if `filament_total` is missing or zero. |

`job.metadata` holds whatever slicer metadata Moonraker parsed from the file, such as `slicer`, `estimated_time`, `filament_total`, and `layer_height`; which keys are present depends on the slicer. Rules don't see live printer state, since the job has already ended by the time a rule runs.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## License

GPLv3. See [LICENSE](LICENSE).
