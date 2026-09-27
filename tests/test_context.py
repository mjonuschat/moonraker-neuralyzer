from neuralyzer import build_context


def test_job_and_tracked_passed_through():
    job = {"filament_used": 12.5, "status": "completed"}
    ctx = build_context(job, tracked=True)
    assert ctx["job"] is job
    assert ctx["tracked"] is True


def test_aux_reshaped_by_provider_and_name():
    job = {
        "filament_used": 0,
        "auxiliary_data": [
            {"provider": "power_meter", "name": "energy", "value": 3.2},
            {"provider": "power_meter", "name": "peak_power", "value": 80},
        ],
    }
    ctx = build_context(job, tracked=False)
    assert ctx["aux"]["power_meter"]["energy"] == 3.2
    assert ctx["aux"]["power_meter"]["peak_power"] == 80


def test_aux_empty_when_no_auxiliary_data():
    ctx = build_context({"filament_used": 0}, tracked=False)
    assert ctx["aux"] == {}


def test_progress_computed_from_metadata():
    job = {"filament_used": 5.0, "metadata": {"filament_total": 10.0}}
    ctx = build_context(job, tracked=False)
    assert ctx["progress"] == 0.5


def test_progress_none_when_filament_total_missing():
    ctx = build_context({"filament_used": 5.0, "metadata": {}}, tracked=False)
    assert ctx["progress"] is None


def test_progress_none_when_filament_total_zero():
    job = {"filament_used": 5.0, "metadata": {"filament_total": 0}}
    ctx = build_context(job, tracked=False)
    assert ctx["progress"] is None


def test_progress_none_when_no_metadata_key():
    ctx = build_context({"filament_used": 5.0}, tracked=False)
    assert ctx["progress"] is None


def test_unrelated_job_fields_survive_untouched():
    job = {"filament_used": 0, "some_new_moonraker_field": 42}
    ctx = build_context(job, tracked=False)
    assert ctx["job"]["some_new_moonraker_field"] == 42
