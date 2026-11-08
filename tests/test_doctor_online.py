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


def test_control_checks_the_hub_and_war_room_not_discord_or_modal(defaults, monkeypatch):
    called = []
    for name in dir(doctor):
        if name.startswith("online_") or name == "check_llm_online":
            monkeypatch.setattr(doctor, name, lambda *a, _n=name, **k: called.append(_n))
    cfg = {**defaults, "discord": {**defaults["discord"], "enabled": True}}
    doctor.check_online(cfg, "control")
    assert {"online_hub", "online_serve", "online_kitsu", "online_anthropic"} <= set(called)
    assert not {"online_discord", "online_modal", "online_nas_cut", "online_youtube"} & set(called)
    called.clear()
    doctor.check_online(cfg, "all")
    assert "online_discord" in called and "online_modal" in called and "online_hub" not in called
