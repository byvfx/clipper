import pytest

from clipper.errors import ClipperError
from clipper.media import source_file


def test_source_file_skips_downloads_in_progress(tmp_path):
    with pytest.raises(ClipperError):
        source_file(tmp_path)
    for name in ("source.mp4.part", "source.f137.mp4.ytdl", "source.mp4-Frag12"):
        (tmp_path / name).touch()
    with pytest.raises(ClipperError):
        source_file(tmp_path)
    (tmp_path / "source.mkv").touch()
    assert source_file(tmp_path).name == "source.mkv"
