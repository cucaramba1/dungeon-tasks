"""Голос -> текст через локальный mlx_whisper.

Модель живёт в отдельном процессе CLI: грузится на время расшифровки и сразу
освобождает память. В простое сервис не держит ни байта от Whisper.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger(__name__)
_lock = asyncio.Lock()  # не запускать две модели параллельно на 8 ГБ RAM

# Подсказка модели: так она чаще пишет время цифрами и не путает слова планировщика
PROMPT = "Задачи на день: завтра в 10:00 йога, важно. Срочно позвонить. По будням в 9 почитать."


async def transcribe(audio: Path) -> str:
    async with _lock:
        with tempfile.TemporaryDirectory() as out:
            proc = await asyncio.create_subprocess_exec(
                config.WHISPER_BIN, str(audio),
                "--model", config.WHISPER_MODEL,
                "--language", "ru",
                "--output-format", "txt",
                "--output-dir", out,
                "--verbose", "False",
                "--condition-on-previous-text", "False",
                "--initial-prompt", PROMPT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")},
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0:
                log.error("whisper failed: %s", err.decode(errors="ignore")[-500:])
                raise RuntimeError("Whisper не смог расшифровать голос")
            txt = next(Path(out).glob("*.txt"), None)
            return txt.read_text(encoding="utf-8").strip() if txt else ""
