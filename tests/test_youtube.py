import json

from clipper import youtube

ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"


def test_sign_in_keeps_the_saved_tokens_extra_scopes(tmp_path):
    """Signing in again after a 7-day expiry mustn't drop the analytics grant."""
    token = tmp_path / "youtube_token.json"
    assert youtube.scopes(token) == youtube.SCOPES
    token.write_text(json.dumps({"scopes": [*youtube.SCOPES, ANALYTICS]}), encoding="utf-8")
    assert youtube.scopes(token) == [*youtube.SCOPES, ANALYTICS]
    token.write_text(json.dumps({"scopes": [ANALYTICS, youtube.SCOPES[0]]}), encoding="utf-8")
    assert youtube.scopes(token) == [*youtube.SCOPES, ANALYTICS]
