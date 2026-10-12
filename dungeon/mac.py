"""Громкое напоминание на самом Mac: звук + окно поверх всего."""
from __future__ import annotations

import re
import subprocess
import threading

SOUND = "/System/Library/Sounds/Sosumi.aiff"


def _as_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def popup(title: str, text: str, loud: bool = True, buttons: list[str] | None = None, on_click=None) -> None:
    """Не блокирует: окно само закроется через 2 минуты. on_click(кнопка) вызовется из фонового потока."""
    if loud:
        subprocess.Popen(["afplay", SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    buttons = buttons or ["Ок"]
    btns = "{" + ", ".join(_as_str(b) for b in buttons) + "}"
    script = (f"display dialog {_as_str(text)} with title {_as_str(title)} "
              f"buttons {btns} default button {len(buttons)} with icon caution giving up after 120")
    if on_click is None:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    def wait():
        try:
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=180).stdout
        except Exception:
            return
        m = re.search(r"button returned:([^,]*)", out)
        if m and "gave up:true" not in out:
            on_click(m.group(1).strip())

    threading.Thread(target=wait, daemon=True).start()


def idle_seconds() -> float:
    """Сколько секунд не трогали клавиатуру и мышь. Сразу после открытия крышки — почти 0."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            if '"HIDIdleTime"' in line:
                return int(line.rsplit("=", 1)[1].strip()) / 1e9
    except Exception:
        pass
    return 1e9


def open_url(url: str) -> None:
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
