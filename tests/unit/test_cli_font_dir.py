"""CLI font directory resolution regressions."""

from pathlib import Path

import kollabor.cli as cli


def test_resolve_font_dir_uses_packaged_fonts(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "site-packages"
    kollabor_dir = package_root / "kollabor"
    fonts_dir = package_root / "fonts"
    kollabor_dir.mkdir(parents=True)
    fonts_dir.mkdir()

    monkeypatch.setattr(cli, "__file__", str(kollabor_dir / "cli.py"))

    assert cli._resolve_font_dir() == fonts_dir


def test_resolve_font_dir_falls_back_to_user_fonts(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "site-packages"
    kollabor_dir = package_root / "kollabor"
    home = tmp_path / "home"
    user_fonts = home / "Library" / "Fonts"
    kollabor_dir.mkdir(parents=True)
    user_fonts.mkdir(parents=True)

    monkeypatch.setattr(cli, "__file__", str(kollabor_dir / "cli.py"))
    monkeypatch.setattr(Path, "home", lambda: home)

    assert cli._resolve_font_dir() == user_fonts
