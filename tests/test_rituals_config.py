from datetime import date

from dungeon import rituals

EXPECT = {
    -1003493020432: "Контент", -1003626669139: "Общество мёртвых читателей", -1003116878132: "Волейбол",
    -1003969606547: "Библиотерапия", -1004426706927: "Цели и привычки", -1001595490870: "Лекции по искусству",
    -1002188099155: "Покер", -1003952363073: "D&D", -1002093016263: "Настолки Neverland",
    -1004482307437: "Йога FYSM",
}


def test_all_ten_chats_are_configured():
    chats = {c["id"]: c for c in rituals.cfg()}
    assert set(chats) == set(EXPECT)
    for cid, name in EXPECT.items():
        assert chats[cid]["name"] == name
        assert chats[cid].get("authors") or chats[cid].get("channel"), name


def test_special_rules():
    chats = {c["id"]: c for c in rituals.cfg()}
    volley = chats[-1003116878132]
    assert volley["topics"] == {3: "outdoor", 132: "indoor"} and volley["authors"] == ["alex_viuk"]
    yoga = chats[-1004482307437]
    assert yoga["series"] == "Йога" and yoga["attend_days"] == ["TU", "SU"] and rituals.as_date(yoga["until"]) == date(2026, 10, 31)
    dnd = chats[-1003952363073]
    assert dnd["keep_time"] is True
    assert dnd["series"] == "Настольная игра Dungeons & Dragons" and set(dnd["authors"]) == {"VladimirGoanskiy", "Mortemanes"}
    assert chats[-1003626669139]["fields"] == ["book"] and chats[-1003969606547]["fields"] == ["book"]
    assert chats[-1002188099155]["channel"] and chats[-1001595490870]["channel"]


def test_targeted_chats_are_not_read_by_afisha():
    from dungeon import events
    skip = {c["id"] for c in events.cfg()["targeted"]}
    channels = {c["id"] for c in events.cfg()["channels"]}
    assert skip & channels                       # пересечение есть, и афиша его пропускает (Task 1)
