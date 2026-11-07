from clipper import setup


def test_control_station_roles():
    assert setup.doctor_role(["control"]) == "control"
    assert setup.doctor_role(["control", "recorder"]) == "control"
    assert setup.doctor_role(["control", "worker"]) == "gpu"  # the worker's checks cover control's
    assert setup.doctor_role(["control", "serve", "bot"]) == "all"
    assert setup.control_only(["control"]) and setup.control_only(["recorder", "control"])
    assert not setup.control_only(["control", "worker"]) and not setup.control_only(["recorder"])
    assert setup.extras_for(["control"], "modal") == []
