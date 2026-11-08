"""The phone's way in (#10): `tailscale serve` in front of the page, and whether it is up."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import wiki
from ..config import _root
from ..errors import ClipperError
from .state import token


def tailscale_exe() -> str | None:
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        p = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale" / "tailscale.exe"
        exe = str(p) if p.is_file() else None
    return exe

def _ts(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run([tailscale_exe() or "tailscale", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)

def _json(proc) -> dict:
    """A tailscale --json answer as a dict; anything else (an older CLI, a login prompt, an
    error) counts as an empty one, so the status reads "not up" instead of crashing."""
    try:
        out = json.loads(proc.stdout or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def tailnet_status(cfg: dict) -> dict:
    """Is Tailscale installed and up here, this machine's tailnet name, and the https URL
    `tailscale serve` proxies to the war room (if it does). userspace: tailscaled has no tun
    device (the NAS hub's container), so this machine's own programs can't reach tailnet
    addresses, its own name included."""
    if not tailscale_exe():
        return {"installed": False, "state": "", "name": "", "served": "", "userspace": False}
    st = _json(_ts("status", "--json"))
    out = {"installed": True, "state": st.get("BackendState", ""),
           "name": ((st.get("Self") or {}).get("DNSName") or "").rstrip("."), "served": "",
           "userspace": st.get("TUN") is False}
    port = _root(cfg)["warroom"]["port"]
    sv = _json(_ts("serve", "status", "--json"))
    for hostport, web in (sv.get("Web") or {}).items():  # {"name:443": {"Handlers": {"/": {"Proxy": ...}}}}
        for path, h in (web.get("Handlers") or {}).items():
            if (h.get("Proxy") or "").rstrip("/") in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                out["served"] = f"https://{hostport.removesuffix(':443')}{path}"
    return out

def tailnet(cfg: dict, action: str) -> None:
    """`clipper warroom --tailnet on|off|status`: put the war room on this machine's tailnet
    name over HTTPS (tailnet devices only; nothing on the LAN or the internet)."""
    cfg = _root(cfg)
    wc, tok_file = cfg["warroom"], Path(cfg["paths"]["warroom_token"])
    st = tailnet_status(cfg)
    if not st["installed"]:
        raise ClipperError("Tailscale isn't installed here: install it from https://tailscale.com/download, "
                         f"sign in, and put the phone on the same tailnet ({wiki.page('Phone-access')})")
    if st["state"] != "Running":
        raise ClipperError(f"Tailscale is {st['state'] or 'not running'}: sign in from its tray icon "
                         "(or `tailscale up`), then run this again")
    if action == "off":
        r = _ts("serve", "--https=443", "off")
        print((r.stdout + r.stderr).strip() or "the war room is off the tailnet")
        return
    if action == "on" and not st["served"]:
        if wc["host"] not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            raise ClipperError(f"[warroom] host is {wc['host']}; tailscale serve proxies to 127.0.0.1")
        if not token(cfg):  # the tailnet is the boundary; the token is the second check
            import secrets
            tok_file.parent.mkdir(parents=True, exist_ok=True)
            tok_file.write_text(secrets.token_urlsafe(9), encoding="utf-8")
            print(f"made a war room token in {tok_file} (restart the bot so it asks for it)")
        # Not captured, no timeout: the first time it prints a link to enable Serve / HTTPS for
        # the tailnet and waits until that's done in the browser.
        print("starting tailscale serve (if it shows a link, open it and allow; this waits)", flush=True)
        r = subprocess.run([tailscale_exe(), "serve", "--bg", "--https=443",
                            f"http://127.0.0.1:{wc['port']}"], check=False)
        if r.returncode:
            raise ClipperError("tailscale serve failed (above). If it asks to enable HTTPS, do that in "
                             "the Tailscale admin console (DNS: MagicDNS and HTTPS certificates), then rerun")
        st = tailnet_status(cfg)
    if not st["served"]:
        print(f"Tailscale is up as {st['name']}; the war room isn't on the tailnet "
              "(clipper warroom --tailnet on)")
        return
    key = token(cfg)
    print(f"war room on the tailnet: {st['served']}")
    if key:  # printed here only, never logged: open it once on the phone, the cookie keeps it
        print(f"open once on the phone (signs it in for a year): {st['served'].rstrip('/')}/?key={key}")
    else:
        print("no war room token: any device on the tailnet can open it")
    if not wc["tailnet"]:
        print("set [warroom] tailnet = true in clipper.toml so `clipper doctor` checks it")
