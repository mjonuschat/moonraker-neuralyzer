from neuralyzer import (
    BASE_TOTALS,
    apply_aux_deltas,
    apply_job_delta,
    compute_aux_deltas,
    compute_job_delta,
)


def test_compute_job_delta():
    job = {"total_duration": 120.0, "print_duration": 90.0, "filament_used": 4.5}
    delta = compute_job_delta(job)
    assert delta == {
        "total_jobs": 1,
        "total_time": 120.0,
        "total_print_time": 90.0,
        "total_filament_used": 4.5,
    }


def test_compute_job_delta_missing_fields_default_to_zero():
    delta = compute_job_delta({})
    assert delta["total_time"] == 0
    assert delta["total_filament_used"] == 0
    assert delta["total_jobs"] == 1


def test_apply_job_delta_subtracts_and_returns_applied_amount():
    totals = dict(BASE_TOTALS)
    totals["total_jobs"] = 5
    totals["total_time"] = 500.0
    applied = apply_job_delta(totals, {"total_jobs": 1, "total_time": 120.0})
    assert totals["total_jobs"] == 4
    assert totals["total_time"] == 380.0
    assert applied == {"total_jobs": 1, "total_time": 120.0}


def test_apply_job_delta_clamps_at_zero():
    totals = dict(BASE_TOTALS)
    totals["total_jobs"] = 1
    totals["total_time"] = 10.0
    applied = apply_job_delta(totals, {"total_jobs": 1, "total_time": 120.0})
    assert totals["total_jobs"] == 0
    assert totals["total_time"] == 0
    # Only the amount actually subtracted (10.0) is reported, not 120.0,
    # so re-adding `applied` later can never overshoot past the clamp.
    assert applied == {"total_jobs": 1, "total_time": 10.0}


def test_longest_fields_are_never_touched():
    # compute_job_delta only ever produces the four correctable keys.
    delta = compute_job_delta({"total_duration": 999})
    assert "longest_job" not in delta
    assert "longest_print" not in delta


def test_compute_aux_deltas_matches_by_provider_and_field():
    job = {
        "auxiliary_data": [
            {"provider": "power_meter", "name": "energy", "value": 3.2},
        ]
    }
    aux_totals = [
        {"provider": "power_meter", "field": "energy", "maximum": None, "total": 50.0},
        {"provider": "other", "field": "energy", "maximum": None, "total": 10.0},
    ]
    deltas = compute_aux_deltas(job, aux_totals)
    assert deltas == [(0, 3.2)]


def test_compute_aux_deltas_skips_when_total_is_none():
    job = {"auxiliary_data": [{"provider": "p", "name": "n", "value": 1.0}]}
    aux_totals = [{"provider": "p", "field": "n", "maximum": 5.0, "total": None}]
    assert compute_aux_deltas(job, aux_totals) == []


def test_compute_aux_deltas_skips_non_numeric_value():
    job = {"auxiliary_data": [{"provider": "p", "name": "n", "value": "oops"}]}
    aux_totals = [{"provider": "p", "field": "n", "maximum": None, "total": 5.0}]
    assert compute_aux_deltas(job, aux_totals) == []


def test_apply_aux_deltas_subtracts_and_clamps():
    aux_totals = [{"provider": "p", "field": "n", "maximum": None, "total": 2.0}]
    applied = apply_aux_deltas(aux_totals, [(0, 5.0)])
    assert aux_totals[0]["total"] == 0
    assert applied == [(0, 2.0)]
