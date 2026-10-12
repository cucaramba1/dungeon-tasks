from datetime import datetime, timedelta, timezone

import pytest

from dungeon.parser import parse

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 9, 26, 18, 30, tzinfo=TZ)  # суббота, вечер


def d(day, h=None, mi=0, month=9, year=2026):
    if h is None:
        return datetime(year, month, day, tzinfo=TZ)
    return datetime(year, month, day, h, mi, tzinfo=TZ)


# (фраза, title, due, has_time, quadrant, habit, rrule)
CASES = [
    ("завтра в 10 йога, важно", "Йога", d(27, 10), True, 1, True, None),
    ("йога", "Йога", None, False, 2, True, None),
    ("купить хлеб", "Купить хлеб", None, False, None, False, None),
    ("срочно позвонить маме", "Позвонить маме", None, False, 1, False, None),
    ("позвонить маме завтра в 15:30", "Позвонить маме", d(27, 15, 30), True, None, False, None),
    ("отчёт клиенту в понедельник важно", "Отчёт клиенту", d(28), False, 1, False, None),
    ("отчёт клиенту в пятницу, важно", "Отчёт клиенту", d(2, month=10), False, 2, False, None),
    ("созвон в 7 вечера", "Созвон", d(26, 19), True, None, False, None),
    ("созвон в 9", "Созвон", d(27, 9), True, None, False, None),
    ("созвон в 3", "Созвон", d(27, 15), True, None, False, None),
    ("созвон в 20:00", "Созвон", d(26, 20), True, None, False, None),
    ("выгулять кота через 2 часа", "Выгулять кота", d(26, 20, 30), True, None, False, None),
    ("проверить духовку через полчаса", "Проверить духовку", d(26, 19), True, None, False, None),
    ("через 15 минут выключить чайник", "Выключить чайник", d(26, 18, 45), True, None, False, None),
    ("оплатить аренду 1.10", "Оплатить аренду", d(1, month=10), False, None, False, None),
    ("оплатить аренду 5 октября важно", "Оплатить аренду", d(5, month=10), False, 2, False, None),
    ("день рождения Саши 3 марта", "День рождения Саши", d(3, month=3, year=2027), False, None, False, None),
    ("каждый день в 7 утра йога", "Йога", d(27, 7), True, 2, True, "FREQ=DAILY"),
    ("медитация каждое утро", "Медитация", d(27, 9), True, 2, True, "FREQ=DAILY"),
    ("по будням в 9 почитать", "Почитать", d(28, 9), True, 2, True, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
    ("спортзал по вторникам и четвергам в 19", "Спортзал", d(29, 19), True, 2, True,
     "FREQ=WEEKLY;BYDAY=TU,TH"),
    ("по выходным уборка", "Уборка", d(26), False, None, False, "FREQ=WEEKLY;BYDAY=SA,SU"),
    ("ритуал: благодарности вечером", "Благодарности", d(26, 19), True, 2, True, None),
    ("привычка холодный душ", "Холодный душ", None, False, 2, True, None),
    ("разобрать почту неважно", "Разобрать почту", None, False, 4, False, None),
    ("ответить в чате сегодня, не важно", "Ответить в чате", d(26), False, 3, False, None),
    ("выучить испанский, важно, не срочно", "Выучить испанский", None, False, 2, False, None),
    ("!1 закрыть кампанию в фб", "Закрыть кампанию в фб", None, False, 1, False, None),
    ("перезапустить креативы #q3", "Перезапустить креативы", None, False, 3, False, None),
    ("позвонить в банк завтра утром", "Позвонить в банк", d(27, 9), True, None, False, None),
    ("послезавтра в 12 обед с Димой", "Обед с Димой", d(28, 12), True, None, False, None),
    ("в полдень запостить рилс", "Запостить рилс", d(27, 12), True, None, False, None),
    ("пробежка на выходных", "Пробежка", d(26), False, 2, True, None),
    ("дописать лендинг к среде, срочно", "Дописать лендинг", d(30), False, 1, False, None),
    ("сделать отчёт в 10.30 завтра", "Сделать отчёт", d(27, 10, 30), True, None, False, None),
    ("купить билеты в 2 раза дешевле", "Купить билеты в 2 раза дешевле", None, False, None, False, None),
    ("главное: сдать налоговую до пятницы", "Сдать налоговую", d(2, month=10), False, 2, False, None),
    ("выпить воды каждые 2 дня", "Выпить воды", d(26), False, 2, True, "FREQ=DAILY;INTERVAL=2"),
    ("завтра в десять утра стоматолог", "Стоматолог", d(27, 10), True, None, False, None),
    ("английский через неделю", "Английский", d(3, month=10), False, 2, True, None),
    ("горит дедлайн по офферу", "По офферу", None, False, 1, False, None),
]


@pytest.mark.parametrize("text,title,due,has_time,quad,habit,rrule", CASES, ids=[c[0] for c in CASES])
def test_parse(text, title, due, has_time, quad, habit, rrule):
    p = parse(text, NOW)
    assert p.title == title
    assert p.due_at == due
    assert p.has_time == has_time
    assert p.quadrant == quad
    assert p.is_habit == habit
    assert p.rrule == rrule


DURATIONS = [
    ("лендинг ~2ч завтра", "Лендинг", 120),
    ("монтаж видео на 1.5 часа, важно", "Монтаж видео", 90),
    ("созвон в 15 на 30 минут", "Созвон", 30),
    ("разбор почты (20м)", "Разбор почты", 20),
    ("почитать на полчаса", "Почитать", 30),
    ("сделать отчёт на 2 часа завтра в 10", "Сделать отчёт", 120),
    ("купить хлеб", "Купить хлеб", None),
]


@pytest.mark.parametrize("text,title,mins", DURATIONS, ids=[d[0] for d in DURATIONS])
def test_duration(text, title, mins):
    p = parse(text, NOW)
    assert p.title == title
    assert p.duration_min == mins


def test_duration_keeps_time():
    p = parse("сделать отчёт на 2 часа завтра в 10", NOW)
    assert p.due_at == d(27, 10) and p.has_time
