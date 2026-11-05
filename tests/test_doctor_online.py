import requests

from clipper import doctor


def test_elevenlabs_unreachable_is_a_finding(defaults, monkeypatch):
    monkeypatch.setenv(defaults["narrate"]["key_env"], "test-key")

    def down(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(requests, "get", down)
    (status, area, what, _), = doctor.collect(doctor.online_elevenlabs, defaults)
    assert (status, area) == ("FAIL", "online") and "ConnectionError" in what


def test_userspace_tailnet_is_checked_at_the_proxy_target(defaults, monkeypatch):
    # The NAS hub's tailscale has no tun device: the container can't fetch its own tailnet
    # name, so doctor asks the address tailscale serve proxies to instead.
    from clipper import warroom

    cfg = {**defaults, "warroom": {**defaults["warroom"], "tailnet": True, "token": "t"}}
    monkeypatch.setattr(warroom, "tailnet_status", lambda c: {
        "installed": True, "state": "Running", "name": "clipper.x.ts.net",
        "served": "https://clipper.x.ts.net/", "userspace": True})
    asked = []

    class Answer:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda url, **k: asked.append(url) or Answer())
    (status, area, what, _), = doctor.collect(doctor.check_tailnet, cfg, False)
    assert (status, area) == ("OK", "warroom") and "userspace" not in what and "proxies to" in what
    assert status == "OK" and asked == [f"http://127.0.0.1:{cfg['warroom']['port']}/manifest.webmanifest"]
