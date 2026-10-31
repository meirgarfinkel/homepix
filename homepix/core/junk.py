"""Photos that may be junk: ads, screenshots, documents, memes and accidental shots, found by what
the search model sees in them. Nothing is remembered about what was kept: the list is worked out
again each time, from the search vectors every photo already has.

Each photo is compared with short descriptions of junk and of ordinary photos worth keeping, the way
the search model sorts a picture into one of several kinds. A photo goes on the list when the junk
descriptions win clearly; it's listed under the kind of junk that fits it best.
"""

import hashlib
import json
import os

import numpy as np

from .config import data_dir
from .search import SearchIndex

# (what the page calls it, descriptions of it)
JUNK = [
    ("Ads and flyers", ["an advertisement", "a flyer", "a promotional poster", "a brochure", "a sale sign"]),
    (
        "Screenshots",
        ["a screenshot of a phone", "a screenshot of a computer screen", "a screenshot of a chat", "a web page"],
    ),
    ("Documents and receipts", ["a receipt", "a scanned document", "a page of printed text", "a form", "a bill"]),
    ("Memes and forwarded images", ["a meme", "a greeting card with text", "an image with a caption"]),
    (
        "Accidental shots",
        [
            "a blurry accidental photo",
            "the floor",
            "the inside of a pocket",
            "a completely black photo",
            "a blank wall",
            "the ceiling",
            "a finger over the camera lens",
        ],
    ),
]
# what ordinary photos show: the more of these, the fewer good photos are mistaken for junk
KEEP = [
    "people",
    "a family",
    "a child",
    "a baby",
    "friends together",
    "a selfie",
    "a portrait",
    "a group photo",
    "a wedding",
    "a birthday party",
    "a holiday celebration",
    "a pet",
    "a dog",
    "a cat",
    "a landscape",
    "the beach",
    "mountains",
    "a city street",
    "a building",
    "a garden",
    "flowers",
    "food on a table",
    "a meal",
    "a trip",
    "a park",
    "a sunset",
    "the sea",
    "a car",
    "a room in a house",
    "kids playing",
]
SHARPNESS = 100.0  # how the search model turns likeness into odds (its training scale)
JUNK_MIN = 0.6  # listed when the junk descriptions take at least this share of the odds

CACHE = "declutter-phrases.npz"  # in the data folder: the descriptions as vectors, worked out once
_prompts = {}  # what they were worked out for -> (vectors of every description, which kind each is: -1 keep)


def _kinds() -> np.ndarray:
    return np.array([k for k, (_, phrases) in enumerate(JUNK) for _ in phrases] + [-1] * len(KEEP))


def _work_out(text_model) -> np.ndarray:
    phrases = [p for _, ps in JUNK for p in ps] + KEEP
    return np.array([text_model.query(p) for p in phrases], np.float32)


def _cache_key() -> str:
    """Changes with the descriptions or the text model's file (so a new model works them out again)."""
    from .ai.models import model_path

    st = os.stat(model_path("clip_text.onnx"))
    return hashlib.sha1(json.dumps([JUNK, KEEP, st.st_size, st.st_mtime]).encode()).hexdigest()


def _descriptions(index: SearchIndex):
    """The descriptions as vectors, and their kinds. Turning 55 descriptions into vectors takes the
    text model a second or two, so for the real model they're kept in the data folder after the
    first time; the model itself isn't even loaded after that."""
    from .ai.clip import ClipText

    model = index._text
    if model is not None and not isinstance(model, ClipText):  # a stand-in (tests): not kept
        if id(model) not in _prompts:
            _prompts[id(model)] = (_work_out(model), _kinds())
        return _prompts[id(model)]
    try:
        key = _cache_key()
    except OSError:  # no model yet: it's downloaded first
        key = None
    if key and key in _prompts:
        return _prompts[key]
    path = data_dir() / CACHE
    try:
        with np.load(path) as kept:
            if key and str(kept["key"]) == key:
                _prompts[key] = (kept["vecs"].astype(np.float32), _kinds())
                return _prompts[key]
    except (OSError, KeyError, ValueError):
        pass
    vecs = _work_out(index.text_model())
    key = key or _cache_key()
    tmp = path.with_name(f"{CACHE}.{os.getpid()}.tmp.npz")
    np.savez(tmp, key=np.array(key), vecs=vecs)
    os.replace(tmp, path)
    _prompts[key] = (vecs, _kinds())
    return _prompts[key]


def warm(index: SearchIndex):
    """Get ready ahead of the first visit: the photos' vectors and the descriptions'. Only with the
    text model already here: nothing is downloaded for it (the first visit does that)."""
    from .ai.models import model_path

    if model_path("clip_text.onnx").exists():
        index.load_vectors()
        _descriptions(index)


def junk_odds(mat: np.ndarray, index: SearchIndex) -> tuple[np.ndarray, np.ndarray]:
    """For each search vector in mat: the share of the odds the junk descriptions take (0 to 1), and
    the kind of junk (index into JUNK) that fits it best."""
    vecs, kinds = _descriptions(index)
    if not len(mat):
        return np.zeros(0, np.float32), np.zeros(0, np.int64)
    logits = SHARPNESS * (mat @ vecs.T)
    logits -= logits.max(axis=1, keepdims=True)
    odds = np.exp(logits)
    odds /= odds.sum(axis=1, keepdims=True)
    junk = kinds >= 0
    per_kind = np.stack([odds[:, kinds == k].sum(axis=1) for k in range(len(JUNK))], axis=1)
    return odds[:, junk].sum(axis=1), per_kind.argmax(axis=1)


def find(index: SearchIndex, db) -> list[tuple[str, list[dict]]]:
    """[(kind of junk, [photo rows with "score"], most likely junk first)], in JUNK's order, kinds with
    nothing in them left out. Photos in excluded folders, Live Photos' clips, and photos with someone
    named in them aren't listed."""
    rows, ids, mat = index.load_vectors()
    keep = np.array([r["kind"] != "motion" for r in rows], bool)
    if len(index.hidden) == len(rows):
        keep &= ~index.hidden
    named = [r[0] for r in db.execute("SELECT DISTINCT photo_id FROM faces WHERE person_id IS NOT NULL")]
    keep &= ~np.isin(ids, named)
    sel = np.flatnonzero(keep)
    score, kind = junk_odds(mat[sel], index)
    found = [[] for _ in JUNK]
    for i in np.argsort(-score, kind="stable"):
        if score[i] < JUNK_MIN:
            break
        found[kind[i]].append({**rows[sel[i]], "score": float(score[i])})
    return [(JUNK[k][0], found[k]) for k in range(len(JUNK)) if found[k]]
