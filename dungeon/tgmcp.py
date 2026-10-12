"""Мини-клиент к локальному telegram-mcp (launchd, HTTP 127.0.0.1:8765).

Только чтение истории каналов. Своей Telegram-сессии сервису задач не нужно.
"""
from __future__ import annotations

import json
import logging

import aiohttp

from . import config

log = logging.getLogger(__name__)
URL = config.env("TGMCP_URL", "http://127.0.0.1:8765/mcp")


async def call(tool: str, args: dict, timeout: int = 30) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.post(URL, json=body, headers=headers) as r:
            raw = await r.text()
    # ответ приходит SSE-событием: строка "data: {...}"
    payload = next((json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")), None)
    if payload is None:
        payload = json.loads(raw)
    if "error" in payload:
        raise RuntimeError(payload["error"])
    text = payload["result"]["content"][0]["text"]
    data = json.loads(text)
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], str):
        data = json.loads(data["result"])  # у некоторых инструментов двойная упаковка
    return data


async def history(chat_id: int, limit: int = 30) -> list[dict]:
    data = await call("get_history", {"chat_id": chat_id, "limit": limit})
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    raise RuntimeError(f"telegram-mcp: неожиданный ответ {str(data)[:200]}")


def restart_service() -> None:
    """Перезапуск той же launchd-службы telegram-mcp (вторую копию не поднимаем)."""
    import os
    import subprocess
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.cucaramba.telegram-mcp-server"],
                   capture_output=True, timeout=30)
