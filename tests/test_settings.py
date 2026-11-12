"""Settings saved from several threads at once: every save lands whole, and none fails."""

import json
import threading

from homepix.core.settings import load_settings, save_settings, settings_path


def test_saves_from_several_threads_at_once(pf):
    errors = []

    def save_many(n):
        try:
            for i in range(50):
                save_settings({"folders": [], "who": n, "i": i})
        except Exception as e:  # (what used to happen: one save moved the other's file away)
            errors.append(e)

    threads = [threading.Thread(target=save_many, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert json.loads(settings_path().read_text(encoding="utf-8"))["i"] == 49  # (whole: it reads)
    assert load_settings()["i"] == 49
    assert [p.name for p in settings_path().parent.iterdir() if p.suffix == ".tmp"] == []


def test_changes_from_two_threads_are_never_lost(pf):
    """A background check noting its status while the window saves a new automation: both changes stay
    (each used to read, change and save on its own, and one could write over the other)."""
    from homepix.core.autoimport import set_status
    from homepix.core.settings import changing_settings

    def note():
        for i in range(300):
            set_status("rule", checked=str(i))

    t = threading.Thread(target=note)
    t.start()
    for i in range(300):
        with changing_settings() as s:
            s.setdefault("added", []).append(i)
    t.join()
    s = load_settings()
    assert s["added"] == list(range(300))
    assert s["import_status"]["rule"]["checked"] == "299"


def test_a_damaged_settings_file_is_never_saved_over_as_empty(pf):
    save_settings({"folders": ["/photos"], "theme": "light"})
    save_settings({"folders": ["/photos", "/more"], "theme": "light"})
    settings_path().write_text('{"folders": ["/pho', encoding="utf-8")  # (cut off)
    s = load_settings()
    assert s["folders"] == ["/photos"]  # the copy kept by the save before
    assert any(p.name.startswith("settings-unreadable-") for p in settings_path().parent.iterdir())
    assert json.loads(settings_path().read_text(encoding="utf-8"))["folders"] == ["/photos"]


def test_a_damaged_phones_file_doesnt_unpair_every_phone(pf):
    from homepix.core import phones

    phones.save_phones([phones.Phone("Pixel", "r1")])
    phones.save_phones([phones.Phone("Pixel", "r1"), phones.Phone("Tab", "r2")])
    path = phones._phones_file()
    path.write_text("[{", encoding="utf-8")
    assert [p.name for p in phones.load_phones()] == ["Pixel"]
    phones.touch(phones.load_phones()[0].id)  # (a save: it must not write an empty list)
    assert [p.name for p in phones.load_phones()] == ["Pixel"]
