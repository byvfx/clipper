"""Discord cards (clipper/notify.py): publishing to Kitsu and posting the cards are two steps,
joined here and nowhere else, so review.py never imports Discord."""
from pathlib import Path

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
