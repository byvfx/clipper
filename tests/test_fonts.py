from clipper import draw


def test_first_font_falls_back_to_the_file_name(tmp_path, monkeypatch):
    # The defaults name Windows paths; the NAS containers have the same files in a font folder.
    (tmp_path / "msfonts").mkdir()
    (tmp_path / "msfonts" / "Impact.ttf").write_bytes(b"")
    monkeypatch.setattr(draw, "font_dirs", lambda: [tmp_path])
    draw.installed_fonts.cache_clear()
    try:
        here = tmp_path / "georgia.ttf"
        here.write_bytes(b"")
        assert draw.first_font(["", str(here), "Z:/NoSuchDir/Fonts/impact.ttf"]) == str(here)
        assert draw.first_font(["Z:/NoSuchDir/Fonts/impact.ttf"]) == str(tmp_path / "msfonts" / "Impact.ttf")
        assert draw.first_font(["Z:\\NoSuchDir\\Fonts\\IMPACT.TTF"]) == str(tmp_path / "msfonts" / "Impact.ttf")
        assert draw.first_font(["Z:/NoSuchDir/Fonts/bahnschrift.ttf"]) is None
        # order wins: an installed Impact before a fallback that exists as given
        assert draw.first_font(["Z:/NoSuchDir/Fonts/impact.ttf", str(here)]) == str(tmp_path / "msfonts" / "Impact.ttf")
    finally:
        draw.installed_fonts.cache_clear()
