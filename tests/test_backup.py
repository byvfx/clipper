"""`clipper backup` (#204): one zip of the taste layer; secrets only when asked, never the
recordings, and old zips dropped past [backup] keep."""
import os
import time
import zipfile

import pytest

from clipper import backup, config, jsonstate, serve
from clipper.errors import ClipperError


@pytest.fixture
def cfg(defaults, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    defaults["_dir"] = root
    (root / "clipper.toml").write_text("[meta]\n", encoding="utf-8")
    defaults["_file"] = root / "clipper.toml"
    defaults["_local_file"] = None
    for key, name in (("streams", "streams"), ("schedule", "schedule.json"), ("people", "people.json"),
                      ("costs", "logs/costs.jsonl"), ("faces", "faces"), ("voices", "voices"),
                      ("layouts", "layouts")):
        defaults["paths"][key] = root / name
    defaults["thumbnail"]["photos"] = str(root / "photos")
    jsonstate.write(root / "schedule.json", {"entries": []})
    (root / "faces" / "models").mkdir(parents=True)
    (root / "faces" / "models" / "big.onnx").write_bytes(b"x" * 100)
    jsonstate.write(root / "faces" / "known.json", {})
    (root / "photos" / "Andy").mkdir(parents=True)
    jsonstate.write(root / "photos" / "Andy" / "cast.json", {})
    (root / "secrets").mkdir()
    (root / "secrets" / "token.json").write_text("{}", encoding="utf-8")
    (root / "backups").mkdir()
    (root / "backups" / "clipper.toml.before_x").write_text("", encoding="utf-8")
    s = root / "streams" / "kick-1"
    s.mkdir(parents=True)
    for name in ("config.toml", "review.json", "transcript.json"):
        (s / name).write_text("{}", encoding="utf-8")
    (s / "source.mkv").write_bytes(b"x" * 100)
    return defaults


def names(path) -> set[str]:
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_the_zip_holds_the_taste_layer_and_nothing_heavy(cfg):
    out = backup.run(cfg)
    zips = list((cfg["_dir"] / "backups" / "taste").glob("taste-*.zip"))
    assert len(zips) == 1 and "no secrets" in out[0]
    got = names(zips[0])
    assert {"clipper.toml", "schedule.json", "faces/known.json", "photos/Andy/cast.json",
            "backups/clipper.toml.before_x", "streams/kick-1/config.toml", "streams/kick-1/review.json"} <= got
    assert not any(n.startswith(("secrets/", "faces/models/", "backups/taste/")) for n in got)
    assert "streams/kick-1/source.mkv" not in got and "streams/kick-1/transcript.json" not in got


def test_secrets_only_when_asked(cfg):
    backup.run(cfg, with_secrets=True)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    assert "secrets/token.json" in names(z)


def test_a_dry_run_writes_nothing(cfg):
    out = backup.run(cfg, dry_run=True)
    assert out[0].startswith("would write") and not (cfg["_dir"] / "backups" / "taste").exists()


def test_old_zips_go_past_keep(cfg):
    dest = cfg["_dir"] / "backups" / "taste"
    dest.mkdir(parents=True)
    for day in ("01", "02", "03"):
        (dest / f"taste-2026-11-{day}-0900.zip").write_bytes(b"")
    (dest / "taste-2026-11-03-1000.zip.part").write_bytes(b"")   # a run killed mid-write
    cfg["backup"]["keep"] = 2
    out = backup.run(cfg)
    assert len(list(dest.glob("taste-*.zip"))) == 2 and "removed 2 older backups" in out[-1]
    assert not list(dest.glob("*.part"))


def test_no_folder_is_an_error_and_newest_is_none(cfg):
    cfg["backup"]["folder"] = ""
    with pytest.raises(ClipperError, match="no backup folder"):
        backup.run(cfg)
    assert backup.newest(cfg) is None


def test_serve_makes_one_only_when_the_newest_is_old(cfg):
    assert serve.backup_due(cfg)
    backup.run(cfg)
    assert not serve.backup_due(cfg)
    p = backup.newest(cfg)
    old = time.time() - 8 * 86400
    os.utime(p, (old, old))
    assert serve.backup_due(cfg)


def test_a_backup_elsewhere_never_takes_the_zips_in_backups(cfg, tmp_path):
    """Copilot on #248: --to another folder still leaves out the taste zips (one may hold secrets)."""
    backup.run(cfg, with_secrets=True)
    backup.run(cfg, to=tmp_path / "offsite")
    (z,) = (tmp_path / "offsite").glob("taste-*.zip")
    assert not any(n.endswith(".zip") for n in names(z))


def test_a_show_pinned_config_backs_up_the_install(cfg):
    """`clipper --show kino backup`: a show's config has no _dir; the install's is used."""
    cfg["shows"] = {"kino": {"channels": ["k"], "overrides": {"thumbnail": {"photos": str(cfg["_dir"] / "kino_photos")}}}}
    cfg["_root"] = cfg
    (cfg["_dir"] / "kino_photos" / "PPP").mkdir(parents=True)
    jsonstate.write(cfg["_dir"] / "kino_photos" / "PPP" / "cast.json", {})
    pinned = {k: v for k, v in config.pin_show(cfg, "kino").items() if k not in ("_dir", "_file", "_local_file")}
    backup.run(pinned)
    (z,) = (cfg["_dir"] / "backups" / "taste").glob("taste-*.zip")
    got = names(z)
    assert "photos/Andy/cast.json" in got and "kino_photos/PPP/cast.json" in got   # every show's photos


def test_one_backup_at_a_time(cfg):
    dest = cfg["_dir"] / "backups" / "taste"
    dest.mkdir(parents=True)
    (dest / "backup.lock").write_text("someone else\n", encoding="utf-8")   # fresh: not stale
    with pytest.raises(ClipperError, match="another backup is writing"):
        backup.run(cfg)
