"""Discord cards (clipper/notify.py): publishing to Kitsu and posting the cards are two steps,
joined here and nowhere else, so review.py never imports Discord."""
from pathlib import Path

import pytest

from clipper import notify, review


def test_publish_and_post_posts_cards_only_for_what_was_published(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "for_stream", lambda cfg, sdir: cfg)
    published, posted = ["c01", "s02"], []
    monkeypatch.setattr(review, "publish", lambda sdir, cfg, force=False, only=None: list(published))
    monkeypatch.setattr(notify, "post", lambda sdir, cfg, keys=None, force=False, note="": posted.append(keys))
    cfg = {"discord": {"enabled": True}}
    assert notify.publish_and_post(tmp_path, cfg) == ["c01", "s02"]
    assert posted == [["c01", "s02"]]

    posted.clear()
    notify.publish_and_post(tmp_path, cfg, post_cards=False)          # --no-notify: Kitsu only
    notify.publish_and_post(tmp_path, {"discord": {"enabled": False}})  # no Discord configured
    published.clear()
    notify.publish_and_post(tmp_path, cfg)                             # nothing new in Kitsu
    assert posted == []


def test_publish_and_post_passes_the_selection_through(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "for_stream", lambda cfg, sdir: cfg)
    seen = {}

    def publish(sdir: Path, cfg: dict, force: bool = False, only=None):
        seen.update(sdir=sdir, force=force, only=only)
        return []
    monkeypatch.setattr(review, "publish", publish)
    notify.publish_and_post(tmp_path, {"discord": {"enabled": True}}, force=True, only=["picks", "shorts"])
    assert seen == {"sdir": tmp_path, "force": True, "only": ["picks", "shorts"]}


def test_publish_and_post_reads_the_streams_own_discord_setting(monkeypatch, tmp_path):
    """Copilot on #172: the flag comes from the stream-resolved config (a [shows.<key>.discord]
    or streams/<id>/config.toml override), the one publish and post see, not the caller's."""
    resolved = {"discord": {"enabled": False}}
    monkeypatch.setattr(notify, "for_stream", lambda cfg, sdir: resolved)
    monkeypatch.setattr(review, "publish", lambda sdir, cfg, force=False, only=None: ["c01"] if cfg is resolved else [])
    posted = []
    monkeypatch.setattr(notify, "post", lambda sdir, cfg, keys=None, force=False, note="": posted.append(keys))
    assert notify.publish_and_post(tmp_path, {"discord": {"enabled": True}}) == ["c01"]
    assert posted == []                       # the stream turned Discord off
    resolved["discord"]["enabled"] = True
    notify.publish_and_post(tmp_path, {"discord": {"enabled": False}})
    assert posted == [["c01"]]                # the stream turned it on


def test_invite_adds_a_discord_id_to_the_allowlist_once(tmp_path):
    """`clipper review invite`: the id lands in clipper.toml's [discord] allowed_user_ids, once; a
    non-id is refused with the recipe."""
    from clipper import config
    from clipper.errors import ClipperError

    path = tmp_path / "clipper.toml"
    path.write_text('[discord]\nallowed_user_ids = [   # a multi-line array (Copilot on #181)\n'
                    '  "111111111111111111",\n]\nenabled = true\n', encoding="utf-8")
    cfg = config.load(path, use_local=False)
    ids, added = notify.invite(cfg, "222222222222222222")
    assert added and ids == ["111111111111111111", "222222222222222222"]
    text = path.read_text(encoding="utf-8")
    assert '"222222222222222222"' in text and "enabled = true" in text
    cfg = config.load(path, use_local=False)
    assert notify.invite(cfg, "222222222222222222") == (ids, False)
    with pytest.raises(ClipperError, match="user id"):
        notify.invite(cfg, "bob")


def test_invite_refuses_when_another_layer_sets_the_allowlist(tmp_path):
    """clipper.local.toml (or a show, or an env var) setting allowed_user_ids would win over the
    shared file: the invite says where the id has to go instead of writing a list nobody reads."""
    from clipper import config
    from clipper.errors import ClipperError

    path = tmp_path / "clipper.toml"
    path.write_text('[discord]\nallowed_user_ids = ["111111111111111111"]\n', encoding="utf-8")
    (tmp_path / config.LOCAL_CONFIG).write_text('[discord]\nallowed_user_ids = ["333333333333333333"]\n',
                                                 encoding="utf-8")
    with pytest.raises(ClipperError, match="clipper.local.toml"):
        notify.invite(config.load(path), "222222222222222222")
    assert '"222222222222222222"' not in path.read_text(encoding="utf-8")
