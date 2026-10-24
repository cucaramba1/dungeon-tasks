"""Игровой движок подземелья: XP, уровни, HP, золото, стрики ритуалов, ачивки."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timedelta

from .db import DB, iso, parse_iso, utcnow

ZONES = {
    1: ("🔥", "Огненные залы"),
    2: ("📜", "Эльфийская библиотека"),
    3: ("👺", "Гоблинские поручения"),
    4: ("🌫", "Туманное болото"),
    None: ("📥", "Сундук у входа"),
}
ZONE_MULT = {1: 2.0, 2: 1.5, 3: 0.5, 4: 0.5, None: 1.0}
TITLES = [
    (1, "Подмастерье гнома"), (5, "Страж тоннелей"), (10, "Рунный кузнец"),
    (18, "Эльфийский следопыт"), (28, "Архимаг"), (40, "Повелитель подземелья"),
]
MAX_HP = 100
BOSS_XP, BOSS_GOLD = 300, 100
FOCUS_XP = 20
CHECKLIST_XP = 15
HABIT_MISS_HP = 10
DECAY, DECAY_FLOOR, DECAY_DAYS = 0.9747, 0.75, 14  # частый ритуал даёт меньше (Habitica scoreTask.js), но не ниже 75%


@dataclass(frozen=True)
class Ach:
    code: str
    icon: str
    name: str
    desc: str


ACHIEVEMENTS = [
    Ach("first_blood", "🗡", "Первая кровь", "Закрыть первую задачу"),
    Ach("goblin_slayer", "👺", "Гоблинобой", "Закрыть 10 задач"),
    Ach("legend", "🏰", "Легенда тоннелей", "Закрыть 100 задач"),
    Ach("fire_tamer", "🔥", "Укротитель пламени", "10 задач из Огненных залов"),
    Ach("elf_wisdom", "📜", "Эльфийская мудрость", "10 задач из Эльфийской библиотеки"),
    Ach("dwarf_discipline", "⛏", "Гномья дисциплина", "Стрик ритуала 7 дней"),
    Ach("rune_master", "🔮", "Мастер рун", "Стрик ритуала 30 дней"),
    Ach("first_trophy", "🏆", "Первый трофей", "Одолеть босса недели"),
    Ach("dragon_slayer", "🐉", "Истребитель драконов", "Одолеть 5 боссов"),
    Ach("night_watch", "🌙", "Ночной страж", "5 задач закрыто после 21:00"),
    Ach("morning_dew", "🌿", "Утренняя роса", "5 задач закрыто до 9:00"),
    Ach("clean_chest", "📦", "Пустой сундук", "Инбокс пуст три вечера подряд"),
    Ach("first_raid", "⚔", "Первый рейд", "Завершить фокус-сессию"),
    Ach("raid_master", "🛡", "Мастер рейдов", "20 фокус-сессий"),
    Ach("swamp_drainer", "🪣", "Осушитель болот", "Выкинуть 10 задач из болота"),
    Ach("dwarf_routine", "☀️", "Рутина гнома", "7 утренних чек-листов закрыто целиком"),
    Ach("night_patrol", "🌙", "Вечерний дозор", "7 вечерних чек-листов закрыто целиком"),
    Ach("phoenix", "🔥", "Феникс", "Восстать после падения и вернуть 100 HP"),
]
ACH_BY_CODE = {a.code: a for a in ACHIEVEMENTS}


def to_next(level: int) -> int:
    """XP от уровня до следующего, кривая Habitica (statHelpers.js): быстрые первые уровни,
    дальше рост почти линейный, а не квадратичный, как было (Архимаг 28 требовал 18 900 XP)."""
    if level < 5:
        return 25 * level
    if level == 5:
        return 150
    return 10 * round((0.25 * level ** 2 + 10 * level + 139.75) / 10)


@lru_cache(maxsize=None)
def level_threshold(level: int) -> int:
    """Сколько всего XP нужно, чтобы достичь уровня."""
    return sum(to_next(k) for k in range(1, level))


def level_for(xp: int) -> int:
    lvl = 1
    while level_threshold(lvl + 1) <= xp:
        lvl += 1
    return lvl


def title_for(level: int) -> str:
    return [t for lv, t in TITLES if lv <= level][-1]


class Game:
    def __init__(self, db: DB, clock=None):
        self.db = db
        self.clock = clock or (lambda: datetime.now().astimezone())

    # --- состояние героя ---
    def hero(self) -> dict:
        h = dict(self.db.one("SELECT * FROM hero WHERE id=1"))
        lvl = level_for(h["xp"])
        h.update(level=lvl, title=title_for(lvl), next_at=level_threshold(lvl + 1),
                 cur_at=level_threshold(lvl), max_hp=MAX_HP)
        best = self.db.one("SELECT MAX(current) AS c FROM streaks")
        h["best_streak"] = best["c"] or 0
        return h

    def _add(self, xp: int = 0, hp: int = 0, gold: int = 0) -> list[str]:
        """Меняет статы, возвращает сообщения о левелапе/падении."""
        msgs = []
        h = self.hero()
        before = h["level"]
        new_hp = max(0, min(MAX_HP, h["hp"] + hp))
        self.db.x("UPDATE hero SET xp=xp+?, hp=?, gold=MAX(0, gold+?) WHERE id=1", (xp, new_hp, gold))
        after = level_for(h["xp"] + xp)
        if after > before:
            msgs.append(f"⬆️ УРОВЕНЬ {after}! Теперь ты — {title_for(after)}.")
        if new_hp <= 0:
            self.db.x("UPDATE hero SET hp=50, gold=gold/2, fallen=1 WHERE id=1")
            self._event("fall")
            msgs.append("💀 Ты пал в бою. Гномы вытащили тебя из тоннеля: HP 50, половина золота потеряна.")
        elif new_hp >= MAX_HP and h["fallen"]:
            self.db.x("UPDATE hero SET fallen=0 WHERE id=1")
            msgs += self._unlock("phoenix")
        return msgs

    def _event(self, kind: str, task_id=None, quadrant=None, xp=0):
        self.db.x("INSERT INTO events(kind, task_id, quadrant, xp, at) VALUES(?,?,?,?,?)",
                  (kind, task_id, quadrant, xp, iso(utcnow())))

    def _unlock(self, code: str) -> list[str]:
        if self.db.one("SELECT 1 FROM achievements WHERE code=?", (code,)):
            return []
        self.db.x("INSERT INTO achievements(code, unlocked_at) VALUES(?,?)", (code, iso(utcnow())))
        a = ACH_BY_CODE[code]
        return [f"🏅 Ачивка: {a.icon} «{a.name}» — {a.desc}"]

    # --- расчёт награды ---
    def xp_for(self, task) -> int:
        base = 15 if task["is_habit"] else 10
        xp = base * ZONE_MULT[task["quadrant"]]
        due = parse_iso(task["due_at"])
        if task["quadrant"] == 2 and due and due - datetime.now(due.tzinfo) > timedelta(hours=24):
            xp += 5  # закрыл важное до того, как оно загорелось
        return int(round(xp))

    # --- события ---
    def habit_decay(self, task) -> float:
        """Сколько раз серия ритуала закрыта за 14 дней: каждый раз базовый XP чуть меньше, фарм лёгкого не окупается."""
        since = iso(utcnow() - timedelta(days=DECAY_DAYS))
        n = self.db.one("SELECT COUNT(*) AS n FROM events e JOIN tasks t ON t.id=e.task_id WHERE e.kind='done' "
                        "AND COALESCE(t.series_id, t.id)=? AND e.at>=?", (task["series_id"] or task["id"], since))["n"]
        return max(DECAY_FLOOR, DECAY ** n)

    def complete(self, task) -> tuple[int, list[str]]:
        xp = self.xp_for(task)
        msgs: list[str] = []
        streak = 0
        if task["is_habit"]:
            xp = int(round(xp * self.habit_decay(task)))
            streak = self._streak_hit(task)
            xp += min(streak, 10)
        gold = xp // 5
        if task["is_boss"]:
            xp += BOSS_XP
            gold += BOSS_GOLD
            self._event("boss", task["id"], task["quadrant"], BOSS_XP)
            msgs.append(f"🐉 БОСС ПОВЕРЖЕН! Сундук: +{BOSS_XP} XP, +{BOSS_GOLD} 🪙")
        self._event("done", task["id"], task["quadrant"], xp)
        msgs = self._add(xp=xp, hp=5 if task["is_habit"] else 2, gold=gold) + msgs
        if streak >= 2:
            msgs.append(f"🔥 Стрик рун: {streak} дн.")
        msgs += self.check_achievements()
        return xp, msgs

    def miss_habit(self, task, hp: int | None = None) -> list[str]:
        sid = task["series_id"] or task["id"]
        self.db.x("UPDATE streaks SET current=0 WHERE series_id=?", (sid,))
        self._event("missed", task["id"], task["quadrant"])
        return self._add(hp=-(HABIT_MISS_HP if hp is None else hp))

    def deleted(self, task) -> list[str]:
        if task["quadrant"] == 4:
            self._event("drain", task["id"], 4)
            return self.check_achievements()
        return []

    def focus_done(self) -> tuple[int, list[str]]:
        self._event("focus", xp=FOCUS_XP)
        msgs = self._add(xp=FOCUS_XP, gold=FOCUS_XP // 5)
        return FOCUS_XP, msgs + self.check_achievements()

    def checklist_done(self, kind: str) -> list[str]:
        self._event(f"checklist_{kind}", xp=CHECKLIST_XP)
        head = "☀️ Утренний" if kind == "morning" else "🌙 Вечерний"
        return [f"{head} чек-лист закрыт: +{CHECKLIST_XP} XP"] + \
            self._add(xp=CHECKLIST_XP, hp=3, gold=3) + self.check_achievements()

    def inbox_check(self, inbox_empty: bool) -> list[str]:
        """Вызывается вечером: считаем пустые инбоксы подряд."""
        n = int(self.db.get("inbox_zero_streak", "0") or 0)
        n = n + 1 if inbox_empty else 0
        self.db.set("inbox_zero_streak", n)
        return self._unlock("clean_chest") if n >= 3 else []

    def _streak_hit(self, task) -> int:
        sid = task["series_id"] or task["id"]
        today = self.clock().date()
        row = self.db.one("SELECT * FROM streaks WHERE series_id=?", (sid,))
        if row is None:
            cur = 1
            self.db.x("INSERT INTO streaks(series_id, title, current, best, last_date) VALUES(?,?,?,?,?)",
                      (sid, task["title"], 1, 1, today.isoformat()))
            return cur
        last = datetime.fromisoformat(row["last_date"]).date() if row["last_date"] else None
        if last == today:
            return row["current"]
        # Пропуски обнуляют стрик в miss_habit. Здесь рвём его только по разрыву дней,
        # кроме ритуалов «по вторникам» и «каждые N дней»: у них разрыв — норма.
        sparse = bool(task["rrule"]) and ("BYDAY" in task["rrule"] or "INTERVAL" in task["rrule"])
        if last and ((today - last).days == 1 or sparse):
            cur = row["current"] + 1
        else:
            cur = 1
        self.db.x("UPDATE streaks SET current=?, best=MAX(best, ?), last_date=?, title=? WHERE series_id=?",
                  (cur, cur, today.isoformat(), task["title"], sid))
        return cur

    def check_achievements(self) -> list[str]:
        msgs: list[str] = []
        c = lambda sql, a=(): self.db.one(sql, a)[0]  # noqa: E731
        done = c("SELECT COUNT(*) FROM events WHERE kind='done'")
        rules = {
            "first_blood": done >= 1,
            "goblin_slayer": done >= 10,
            "legend": done >= 100,
            "fire_tamer": c("SELECT COUNT(*) FROM events WHERE kind='done' AND quadrant=1") >= 10,
            "elf_wisdom": c("SELECT COUNT(*) FROM events WHERE kind='done' AND quadrant=2") >= 10,
            "dwarf_discipline": c("SELECT COALESCE(MAX(best),0) FROM streaks") >= 7,
            "rune_master": c("SELECT COALESCE(MAX(best),0) FROM streaks") >= 30,
            "first_trophy": c("SELECT COUNT(*) FROM events WHERE kind='boss'") >= 1,
            "dragon_slayer": c("SELECT COUNT(*) FROM events WHERE kind='boss'") >= 5,
            "first_raid": c("SELECT COUNT(*) FROM events WHERE kind='focus'") >= 1,
            "raid_master": c("SELECT COUNT(*) FROM events WHERE kind='focus'") >= 20,
            "swamp_drainer": c("SELECT COUNT(*) FROM events WHERE kind='drain'") >= 10,
            "dwarf_routine": c("SELECT COUNT(*) FROM events WHERE kind='checklist_morning'") >= 7,
            "night_patrol": c("SELECT COUNT(*) FROM events WHERE kind='checklist_evening'") >= 7,
        }
        hours = [parse_iso(r["at"]).astimezone().hour for r in self.db.q("SELECT at FROM events WHERE kind='done'")]
        rules["night_watch"] = sum(h >= 21 for h in hours) >= 5
        rules["morning_dew"] = sum(h < 9 for h in hours) >= 5
        for code, ok in rules.items():
            if ok:
                msgs += self._unlock(code)
        return msgs

    def achievements(self) -> list[dict]:
        got = {r["code"]: r["unlocked_at"] for r in self.db.q("SELECT * FROM achievements")}
        return [dict(code=a.code, icon=a.icon, name=a.name, desc=a.desc, unlocked_at=got.get(a.code))
                for a in ACHIEVEMENTS]
