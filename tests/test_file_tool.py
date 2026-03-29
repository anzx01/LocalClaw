"""Tests for file tool path resolution behavior."""

from pathlib import Path

from localclaw.tools.file_tool import FileListTool


def test_file_list_resolves_root_desktop_alias(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    desktop_dir = home_dir / "Desktop"
    desktop_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    tool = FileListTool(base_dir=tmp_path / "workspace")
    resolved = tool._resolve_path("/Desktop")

    assert resolved == desktop_dir


def test_file_list_resolves_nested_desktop_alias(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    desktop_dir = home_dir / "Desktop"
    desktop_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    tool = FileListTool(base_dir=tmp_path / "workspace")
    resolved = tool._resolve_path("/Desktop/AI量化工具.txt")

    assert resolved == desktop_dir / "AI量化工具.txt"

