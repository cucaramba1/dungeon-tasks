"""Тесты не должны трогать настоящий vault, запускать claude, открывать окна и слать уведомления."""
import pytest


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from dungeon import config, mac
    monkeypatch.setattr(config, "OBSIDIAN_DIR", tmp_path / "vault")
    monkeypatch.setattr(config, "GOALS_NOTE", tmp_path / "vault" / "Мои цели.md")
    monkeypatch.setattr(config, "CLAUDE_BIN", "/usr/bin/false")
    monkeypatch.setattr(config, "WEB_KEY", "")
    monkeypatch.setattr(mac, "popup", lambda *a, **k: None)
    monkeypatch.setattr(mac, "open_url", lambda url: pytest.fail("открыл браузер в тесте"))
    monkeypatch.setattr(mac, "idle_seconds", lambda: 1e9)
