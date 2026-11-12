"""The phone app does some of what the computer does, in Kotlin: the tokenizer, splitting search matches,
the quick fingerprint, reading the pairing link, reading a library copy (the database's version and
columns). android/app/src/test/resources/expected.json holds what
the computer's code makes; the phone's tests check they make the same. This test makes it again from the
computer's code and fails when the committed one differs, so a change here can't leave the phone behind.

To write it again: HOMEPIX_WRITE_FIXTURES=1 pytest tests/test_phone_agrees.py, then run the phone's tests.
"""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from homepix.core import phones, search
from homepix.core.util import quick_hash

EXPECTED = Path(__file__).parent.parent / "android" / "app" / "src" / "test" / "resources" / "expected.json"
TOKENIZER = Path(__file__).parent.parent / "android" / "app" / "src" / "main" / "assets" / "clip_tokenizer.json"


def _made(committed: dict, tmp_path, monkeypatch) -> dict:
    out = {}
    if TOKENIZER.is_file():  # (downloaded by the phone's build; without it, the tokens can't be made here)
        from homepix.core.ai.clip_tokens import ClipTokenizer

        tok = ClipTokenizer(TOKENIZER)
        out["tokens"] = [[text, tok.encode(text)] for text, _ in committed["tokens"]]
    else:
        out["tokens"] = committed["tokens"]
    out["scores"] = committed["scores"]
    out["splits"] = [[c.tolist(), n.tolist()] for c, n in (search.split_matches(np.array(s)) for s in out["scores"])]
    out["qhash"] = {}
    for n in (1000, 131072, 300000):
        f = tmp_path / f"{n}.bin"
        f.write_bytes(bytes((i * 31 + 7) % 251 for i in range(n)))
        out["qhash"][str(n)] = quick_hash(f, n)
    # a pairing link, as the computer writes it, and what the phone must read from it
    monkeypatch.setattr(phones, "addresses", lambda: ["192.168.1.20", "10.0.0.7"])
    monkeypatch.setattr(phones, "computer_name", lambda: "Den PC & co")
    ident = type("I", (), {"fingerprint": "ab" * 32})()
    monkeypatch.setattr(phones, "identity", lambda: ident)
    link = phones.qr_text(phones.Pairing("r1", code="c0de-_X"), 47820)
    q = parse_qs(urlparse(link).query)
    out["pair"] = {
        "link": link,
        "addresses": q["a"][0].split(","),
        "port": int(q["p"][0]),
        "fingerprint": ident.fingerprint,
        "code": q["c"][0],
        "name": q["n"][0],
    }
    import sqlite3

    from homepix.core.db import TABLES, VERSION

    TABLES_READ = ("photos", "faces", "people")  # (the ones the phone reads)
    schema = sqlite3.connect(":memory:")
    for create in TABLES.values():
        schema.execute(create)
    out["library"] = {
        "version": VERSION,
        "columns": {t: [r[1] for r in schema.execute(f"PRAGMA table_info({t})")] for t in TABLES_READ},
    }
    schema.close()
    return out


def test_the_phones_expected_values_are_the_computers(tmp_path, monkeypatch):
    committed = json.loads(EXPECTED.read_text(encoding="utf-8"))
    made = _made(committed, tmp_path, monkeypatch)
    if os.environ.get("HOMEPIX_WRITE_FIXTURES"):
        EXPECTED.write_text(json.dumps(made, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pytest.skip("written: run the phone's tests next")
    assert made == committed, "the computer's code changed: write expected.json again (see this file's top)"
