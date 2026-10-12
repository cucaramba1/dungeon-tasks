"""Разовый логин отдельной Telethon-сессии для страховочных напоминаний.

Берёт api_id/api_hash из .env или из ~/mcp-servers/telegram-mcp/.env,
по умолчанию входит через QR (как Telegram Desktop): сканируешь телефоном, код не нужен.
С флагом --phone старый вход по телефону и коду. Пишет TG_INSURANCE_SESSION в .env.
Сессия своя (не та, что у telegram-mcp), поэтому конфликтов ключей не будет.
"""
import asyncio
import getpass
import re
import sys
from pathlib import Path

import qrcode

from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
MCP_ENV = Path.home() / "mcp-servers/telegram-mcp/.env"


def set_env(key: str, value: str) -> None:
    text = ENV.read_text() if ENV.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    ENV.write_text(text)


def show_qr(url: str) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    print("\033[2J\033[H")  # очистить экран, чтобы старый QR не путал
    qr.print_ascii(invert=True)
    print("\nТелефон: Telegram → Настройки → Устройства → «Подключить устройство» → наведи камеру на QR.")
    print("QR живёт ~30 секунд, потом обновится сам. Ctrl+C — выйти.")


async def qr_login(client: TelegramClient) -> None:
    await client.connect()
    while True:
        qr = await client.qr_login()
        show_qr(qr.url)
        try:
            await qr.wait(timeout=30)
            return
        except asyncio.TimeoutError:
            continue  # QR протух, рисуем новый
        except SessionPasswordNeededError:
            pwd = getpass.getpass("Включён облачный пароль (2FA). Введи его (символы не видно): ")
            await client.sign_in(password=pwd)
            return


async def main() -> None:
    env = dotenv_values(ENV) if ENV.exists() else {}
    api_id, api_hash = env.get("TG_API_ID"), env.get("TG_API_HASH")
    if not (api_id and api_hash) and MCP_ENV.exists():
        mcp = dotenv_values(MCP_ENV)
        api_id, api_hash = mcp.get("TELEGRAM_API_ID"), mcp.get("TELEGRAM_API_HASH")
        print("api_id/api_hash взяты из telegram-mcp")
    if not (api_id and api_hash):
        api_id = input("api_id (my.telegram.org/apps): ").strip()
        api_hash = input("api_hash: ").strip()
    client = TelegramClient(StringSession(), int(api_id), api_hash, device_model="Dungeon Tasks", app_version="1.0")
    if "--phone" in sys.argv:
        await client.start()  # спросит телефон, код и пароль 2FA
    else:
        await qr_login(client)
    me = await client.get_me()
    set_env("TG_API_ID", str(api_id))
    set_env("TG_API_HASH", api_hash)
    set_env("TG_INSURANCE_SESSION", client.session.save())
    print(f"Готово: страховка залогинена как {me.first_name}. Перезапусти сервис: scripts/setup.sh restart")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
