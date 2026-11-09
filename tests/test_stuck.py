from dungeon import stuck
from dungeon.db import DB


def mk():
    return DB(":memory:")


def run(db, answers):
    out = None
    for a in answers:
        msg, out = stuck.answer(db, a)
    return msg, out


def test_full_flow():
    db = mk()
    assert "Застрял" in stuck.start(db)
    assert stuck.active(db)["step"] == 0
    msg, res = run(db, ["налоги", "декларация", "скучно", "не будет штрафа", "открыть файл", "2", "кофе"])
    assert res["step"] == "открыть файл" and res["minutes"] == 2 and res["reward"] == "кофе"
    assert res["quadrant"] == 2
    assert stuck.active(db) is None
    assert stuck.to_parsed(res).duration_min == 2


def test_step_over_two_minutes_goes_back():
    db = mk()
    stuck.start(db)
    msg, res = run(db, ["a", "b", "c", "d", "большой шаг", "15"])
    assert res is None and "многовато" in msg
    assert stuck.active(db)["step"] == 4
    assert len(stuck.active(db)["answers"]) == 4
    _, res = run(db, ["мелкий шаг", "1", "награда"])
    assert res["step"] == "мелкий шаг"


def test_two_bounces_in_a_row_give_fallback_step():
    db = mk()
    stuck.start(db)
    run(db, ["дело", "b", "c", "d", "большой шаг", "15", "всё равно большой", "20"])
    assert stuck.active(db)["step"] == 6
    _, res = run(db, ["награда"])
    assert res["minutes"] == 2 and "дело" in res["step"] and res["reward"] == "награда"


def test_minutes_must_be_number():
    db = mk()
    stuck.start(db)
    msg, res = run(db, ["a", "b", "c", "d", "e", "хз"])
    assert res is None and "число" in msg
    assert stuck.active(db)["step"] == 5


def test_start_from_task_skips_first_question_and_keeps_quadrant():
    db = mk()
    q = stuck.start(db, {"id": 7, "title": "отчёт", "quadrant": 1})
    assert "(2/7)" in q
    _, res = run(db, ["два слова", "тяжко", "польза", "шаг", "2", "чай"])
    assert res["task"] == "отчёт" and res["tid"] == 7 and res["quadrant"] == 1


def test_cancel():
    db = mk()
    assert not stuck.cancel(db)
    stuck.start(db)
    assert stuck.cancel(db) and stuck.active(db) is None
