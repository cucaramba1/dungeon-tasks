#!/bin/zsh
# Dungeon Tasks: установка автозапуска и управление сервисом.
#   scripts/setup.sh install | restart | stop | status | logs | uninstall | url
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.cucaramba.dungeon-tasks"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"
DOMAIN="gui/$(id -u)"

env_get() { { grep -E "^$1=" "$ROOT/.env" 2>/dev/null || true; } | head -1 | cut -d= -f2-; }
env_set() {
  if grep -qE "^$1=" "$ROOT/.env"; then
    sed -i '' "s|^$1=.*|$1=$2|" "$ROOT/.env"
  else
    echo "$1=$2" >> "$ROOT/.env"
  fi
}

write_plist() {
  mkdir -p "$ROOT/logs"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>dungeon</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.err.log</string>
</dict>
</plist>
EOF
}

case "${1:-install}" in
  install)
    cd "$ROOT"
    [[ -f .env ]] || cp .env.example .env
    [[ -n "$(env_get WEB_KEY)" ]] || env_set WEB_KEY "$(openssl rand -hex 16)"
    [[ -x "$PY" ]] || uv sync
    "$PY" scripts/make_icons.py >/dev/null
    write_plist
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "✅ Сервис установлен и запущен, стартует сам при входе в систему."
    if command -v tailscale >/dev/null; then
      tailscale serve --bg "$(env_get WEB_PORT || echo 8787)" >/dev/null && echo "✅ tailscale serve включён"
    else
      echo "ℹ️  Tailscale не найден: поставь его, чтобы открыть PWA с iPhone (см. README)."
    fi
    "$0" url
    ;;
  restart) launchctl kickstart -k "$DOMAIN/$LABEL" && echo "🔄 перезапущен" ;;
  stop) launchctl bootout "$DOMAIN/$LABEL" && echo "⏹ остановлен (до следующего install)" ;;
  status) launchctl print "$DOMAIN/$LABEL" | grep -E "state|pid|last exit" ;;
  logs) tail -n 50 -f "$ROOT/logs/dungeon.log" ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "🗑 автозапуск удалён, данные в $ROOT/data не тронуты"
    ;;
  url)
    KEY="$(env_get WEB_KEY)"
    PORT="$(env_get WEB_PORT)"; PORT="${PORT:-8787}"
    echo "💻 На маке:   http://127.0.0.1:$PORT/?key=$KEY"
    if command -v tailscale >/dev/null; then
      HOST="$(tailscale status --json 2>/dev/null | "$PY" -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
      [[ -n "$HOST" ]] && echo "📱 На iPhone: https://$HOST/?key=$KEY"
    fi
    ;;
  *) echo "usage: $0 install|restart|stop|status|logs|uninstall|url"; exit 1 ;;
esac
