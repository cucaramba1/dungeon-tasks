#!/bin/zsh
# Быстрое добавление задачи с мака. Для хоткея через Shortcuts.
#   quick_add.sh "завтра в 10 йога"   — текст аргументом
#   echo "текст" | quick_add.sh       — текст из stdin (Shortcuts: «Надиктовать текст» → этот скрипт)
#   quick_add.sh                      — без текста покажет окно ввода
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$(grep -E '^WEB_KEY=' "$ROOT/.env" | cut -d= -f2-)"
PORT="$(grep -E '^WEB_PORT=' "$ROOT/.env" | cut -d= -f2-)"; PORT="${PORT:-8787}"

TEXT="$*"
[[ -z "$TEXT" && ! -t 0 ]] && TEXT="$(cat)"
if [[ -z "$TEXT" ]]; then
  TEXT="$(osascript -e 'text returned of (display dialog "Новая задача:" default answer "" with title "⚔ Dungeon Tasks" buttons {"Отмена", "Добавить"} default button 2)' 2>/dev/null)" || exit 0
fi
[[ -z "$TEXT" ]] && exit 0

JSON="$(/usr/bin/python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "source": "hotkey"}))' "$TEXT")"
SAY="$(curl -s -m 10 -X POST "http://127.0.0.1:$PORT/api/tasks" -H "X-Key: $KEY" -H 'Content-Type: application/json' -d "$JSON" \
  | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("say","ошибка"))' 2>/dev/null)"
[[ -z "$SAY" ]] && SAY="Сервис не отвечает. scripts/setup.sh status"
osascript -e "display notification \"${SAY//\"/\\\"}\" with title \"⚔ Задача добавлена\""
echo "$SAY"
