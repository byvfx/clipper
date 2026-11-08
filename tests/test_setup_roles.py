from clipper import setup


def test_control_station_roles():
    assert setup.doctor_role(["control"]) == "control"
    assert setup.doctor_role(["control", "recorder"]) == "control"
    assert setup.doctor_role(["control", "worker"]) == "gpu"  # the worker's checks cover control's
    assert setup.doctor_role(["control", "serve", "bot"]) == "all"
    assert setup.control_only(["control"]) and setup.control_only(["recorder", "control"])
    assert not setup.control_only(["control", "worker"]) and not setup.control_only(["recorder"])
    assert setup.extras_for(["control"], "modal") == []



def test_control_station_keys_only_warn(defaults, monkeypatch):
    from clipper import doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(defaults["review"]["token_env"], raising=False)
    defaults["paths"]["anthropic_key"] = defaults["paths"]["people"].parent / "no_key.txt"

    def levels(role):
        return {line[0] for line in doctor.collect(doctor.check_keys, defaults, role)
                if "ANTHROPIC" in line[2] or "Kitsu" in line[2] or "[review] host" in line[2]}

    assert levels("gpu") == {"FAIL"}
    assert levels("control") == {"WARN"}
