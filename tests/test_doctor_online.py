import requests

from clipper import doctor


def test_elevenlabs_unreachable_is_a_finding(defaults, monkeypatch):
    monkeypatch.setenv(defaults["narrate"]["key_env"], "test-key")

    def down(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(requests, "get", down)
    (status, area, what, _), = doctor.collect(doctor.online_elevenlabs, defaults)
    assert (status, area) == ("FAIL", "online") and "ConnectionError" in what
