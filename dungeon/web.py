"""HTTP API + статика PWA. Слушает только localhost; наружу (на iPhone) через `tailscale serve`."""
from __future__ import annotations

import secrets
import tempfile
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, stt, texts
from .app import App
from .db import parse_iso
from .game import ZONES


class NewTask(BaseModel):
    text: str
    source: str = "pwa"


class Patch(BaseModel):
    quadrant: int | None = None
    clear_quadrant: bool = False
    title: str | None = None
    toggle_habit: bool = False
    when: str | None = None       # «завтра в 15» — свободный текст, как в боте
    boss: bool = False


class Snooze(BaseModel):
    minutes: int | None = None
    tomorrow: bool = False


def create_app(app: App) -> FastAPI:
    api = FastAPI(title="Dungeon Tasks", docs_url=None, redoc_url=None)
    svc = app.svc

    def auth(request: Request):
        if not config.WEB_KEY:
            return
        key = request.headers.get("x-key") or request.query_params.get("key") or ""
        if not secrets.compare_digest(key, config.WEB_KEY):
            raise HTTPException(401, "bad key")

    def dump(t) -> dict:
        now = svc.clock()
        d = dict(t)
        due = parse_iso(t["due_at"])
        d.update(icon=ZONES[t["quadrant"]][0], zone=ZONES[t["quadrant"]][1], when=texts.when(t, now),
                 overdue=bool(due and due.astimezone(now.tzinfo).date() < now.date()),
                 repeat=texts.human_rrule(t["rrule"]), dur=texts.dur(t["duration_min"]))
        return d

    # ---------- чтение ----------
    @api.get("/api/state", dependencies=[Depends(auth)])
    def state():
        now = svc.clock()
        today = svc.day(now, include_overdue=True)
        tmr = svc.day(now + timedelta(days=1), include_overdue=False)
        h = svc.game.hero()
        f = svc.focus_current()
        return {
            "now": now.isoformat(),
            "today": [dump(t) for t in today["overdue"] + today["dated"] + today["undated_q1"]],
            "tomorrow": [dump(t) for t in tmr["dated"]],
            "library": [dump(t) for t in today["undated_q2"]],
            "open": [dump(t) for t in svc.open_tasks()],
            "inbox": [dump(t) for t in svc.inbox()],
            "boss": dump(svc.boss()) if svc.boss() else None,
            "hero": h,
            "achievements": svc.game.achievements(),
            "streaks": [dict(s) for s in app.db.q("SELECT * FROM streaks ORDER BY current DESC")],
            "rewards": [dict(r) for r in svc.rewards()],
            "focus": dict(f) if f else None,
            "focus_min": int(app.db.get("focus_min")),
            "zones": {str(k): v for k, v in ZONES.items() if k},
            "checklists": {k: svc.checklist_state(k) for k in ("morning", "evening")},
            "evening_at": app.db.get("evening"),
        }

    # ---------- создание ----------
    @api.post("/api/tasks", dependencies=[Depends(auth)])
    async def add(body: NewTask):
        tasks = svc.create(body.text, body.source)
        await app.created(tasks, notify_tg=False)
        await app.announce(svc.game.check_achievements())
        say = "; ".join(f"{ZONES[t['quadrant']][0]} {t['title']}" + (f", {texts.when(t, svc.clock())}"
                        if t["due_at"] else "") for t in tasks)
        return {"tasks": [dump(t) for t in tasks], "say": say or "Пусто"}

    @api.post("/api/voice", dependencies=[Depends(auth)])
    async def voice(file: UploadFile):
        suffix = Path(file.filename or "a.m4a").suffix or ".m4a"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / f"voice{suffix}"
            path.write_bytes(await file.read())
            try:
                text = await stt.transcribe(path)
            except Exception as e:
                raise HTTPException(500, str(e))
        if not text:
            return {"text": "", "tasks": []}
        tasks = svc.create(text, "voice")
        await app.created(tasks, notify_tg=False)
        return {"text": text, "tasks": [dump(t) for t in tasks]}

    # ---------- изменения ----------
    def must(tid: int):
        t = svc.get(tid)
        if not t:
            raise HTTPException(404, "нет такой задачи")
        return t

    @api.patch("/api/tasks/{tid}", dependencies=[Depends(auth)])
    async def patch(tid: int, body: Patch):
        must(tid)
        if body.quadrant or body.clear_quadrant:
            svc.set_quadrant(tid, None if body.clear_quadrant else body.quadrant)
        if body.title:
            svc.rename(tid, body.title)
        if body.toggle_habit:
            svc.toggle_habit(tid)
        if body.when:
            svc.edit_from_text(tid, body.when)
        if body.boss:
            svc.make_boss(tid)
        await app.changed(tid)
        return dump(svc.get(tid))

    @api.post("/api/tasks/{tid}/done", dependencies=[Depends(auth)])
    async def done(tid: int):
        must(tid)
        xp, msgs = await app.done(tid, notify_tg=True)
        return {"xp": xp, "msgs": msgs}

    @api.post("/api/tasks/{tid}/snooze", dependencies=[Depends(auth)])
    async def snooze(tid: int, body: Snooze):
        must(tid)
        svc.snooze(tid, minutes=body.minutes, tomorrow=body.tomorrow)
        await app.changed(tid)
        return dump(svc.get(tid))

    @api.post("/api/tasks/{tid}/skip", dependencies=[Depends(auth)])
    async def skip(tid: int):
        must(tid)
        await app.skip(tid)
        return {"ok": True}

    @api.delete("/api/tasks/{tid}", dependencies=[Depends(auth)])
    async def delete(tid: int):
        must(tid)
        return {"msgs": await app.delete(tid)}

    # ---------- фокус и таверна ----------
    @api.post("/api/focus", dependencies=[Depends(auth)])
    def focus_start(task_id: int | None = None):
        return dict(svc.focus_start(task_id))

    @api.delete("/api/focus", dependencies=[Depends(auth)])
    def focus_cancel():
        svc.focus_cancel()
        return {"ok": True}

    @api.post("/api/checklist/{kind}/{idx}", dependencies=[Depends(auth)])
    async def checklist_toggle(kind: str, idx: int):
        if kind not in ("morning", "evening"):
            raise HTTPException(404, "нет такого чек-листа")
        state, msgs = svc.checklist_toggle(kind, idx)
        await app.announce(msgs)
        return {"state": state, "msgs": msgs}

    @api.post("/api/buy/{rid}", dependencies=[Depends(auth)])
    def buy(rid: int):
        return {"msg": svc.buy(rid)}

    # ---------- статика ----------
    @api.get("/")
    def index():
        return FileResponse(config.STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @api.get("/sw.js")
    def sw():
        return FileResponse(config.STATIC / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    @api.get("/health")
    def health():
        return JSONResponse({"ok": True})

    @api.middleware("http")
    async def no_stale(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"  # после обновлений айфон не залипнет на старой версии
        return resp

    api.mount("/static", StaticFiles(directory=config.STATIC), name="static")
    return api
