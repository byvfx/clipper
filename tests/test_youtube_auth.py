import json

import pytest

from clipper import youtube
from clipper.errors import ClipperError

ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"


def token(tmp_path, scopes):
    p = tmp_path / "youtube_token.json"
    p.write_text(json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                             "token_uri": "https://oauth2.googleapis.com/token", "scopes": scopes,
                             "expiry": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    return p


def test_signing_in_again_keeps_the_analytics_scope(tmp_path):
    p = token(tmp_path, [*youtube.SCOPES, ANALYTICS])
    assert youtube.scopes(p, youtube.SCOPES) == [*youtube.SCOPES, ANALYTICS]
    assert youtube.scopes(tmp_path / "none.json", youtube.SCOPES) == youtube.SCOPES


def test_a_dead_sign_in_says_how_to_fix_it(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
    monkeypatch.setattr(Credentials, "refresh", refresh)
    paths = {"youtube_token": token(tmp_path, youtube.SCOPES)}
    with pytest.raises(ClipperError, match=r"\(invalid_grant: Token has been expired or revoked\.\); "
                                           r"run: clipper upload --sign-in.*Testing"):
        youtube.credentials(paths)
