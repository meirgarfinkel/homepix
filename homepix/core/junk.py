"""Photos that may be junk: ads, screenshots, documents, memes and accidental shots, found by what
the search model sees in them. Nothing is remembered about what was kept: the list is worked out
again each time, from the search vectors every photo already has.

Each photo is compared with short descriptions of junk and of ordinary photos worth keeping, the way
the search model sorts a picture into one of several kinds. A photo goes on the list when the junk
descriptions win clearly; it's listed under the kind of junk that fits it best.
"""

import numpy as np

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

_prompts = {}  # text model -> (vectors of every description, which kind each is: -1 for keep)


def _descriptions(text_model):
    if id(text_model) not in _prompts:
        vecs, kinds = [], []
        for k, (_, phrases) in enumerate(JUNK):
            vecs += [text_model.query(p) for p in phrases]
            kinds += [k] * len(phrases)
        vecs += [text_model.query(p) for p in KEEP]
        kinds += [-1] * len(KEEP)
        _prompts.clear()  # (one text model per run)
        _prompts[id(text_model)] = (np.array(vecs, np.float32), np.array(kinds))
    return _prompts[id(text_model)]


def junk_odds(mat: np.ndarray, text_model) -> tuple[np.ndarray, np.ndarray]:
    """For each search vector in mat: the share of the odds the junk descriptions take (0 to 1), and
    the kind of junk (index into JUNK) that fits it best."""
    vecs, kinds = _descriptions(text_model)
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
    score, kind = junk_odds(mat[sel], index.text_model())
    found = [[] for _ in JUNK]
    for i in np.argsort(-score, kind="stable"):
        if score[i] < JUNK_MIN:
            break
        found[kind[i]].append({**rows[sel[i]], "score": float(score[i])})
    return [(JUNK[k][0], found[k]) for k in range(len(JUNK)) if found[k]]
