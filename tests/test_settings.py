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
