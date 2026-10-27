"""The library in date order, grouped by month (what the timeline shows), and its folders."""

import os
from collections import Counter
from dataclasses import dataclass, field


@dataclass(slots=True)
class Item:
    id: int
    path: str
    taken: str  # "YYYY-MM-DD HH:MM:SS"
    qhash: str
    kind: str = "photo"


@dataclass(slots=True)
class Month:
    key: str  # "YYYY-MM"
    items: list = field(default_factory=list)
    title: str = ""  # shown instead of the month's name (e.g. a section of search results)

    @property
    def year(self) -> int:
        return int(self.key[:4])

    @property
    def month(self) -> int:
        return int(self.key[5:7])


def load_items(db, where: str = "1=1", params=(), folder=None) -> list[Item]:
    """Every showable item, newest first; folder: only those in it or below it. Photos that couldn't be
    read have no thumbnail to show."""
    if folder is not None:
        prefix = str(folder).rstrip(os.sep) + os.sep
        where, params = f"({where}) AND substr(path, 1, ?) = ?", (*params, len(prefix), prefix)
    rows = db.execute(
        "SELECT id, path, taken, qhash, kind FROM photos "
        f"WHERE error IS NULL AND qhash IS NOT NULL AND ({where}) ORDER BY taken DESC, path DESC",
        params,
    )
    return [Item(r[0], r[1], r[2] or "0000-00-00 00:00:00", r[3], r[4] or "photo") for r in rows]


def group_by_month(items: list[Item]) -> list[Month]:
    """items must be in date order (as load_items returns them)."""
    months = []
    for it in items:
        key = it.taken[:7]
        if not months or months[-1].key != key:
            months.append(Month(key))
        months[-1].items.append(it)
    return months


def item_from_row(r) -> Item:
    return Item(r["id"], r["path"], r["taken"] or "0000-00-00 00:00:00", r["qhash"], r["kind"] or "photo")


def folder_counts(db) -> Counter:
    """{folder: photos directly in it} for every folder holding showable photos."""
    counts = Counter()
    for (path,) in db.execute("SELECT path FROM photos WHERE error IS NULL AND qhash IS NOT NULL"):
        counts[os.path.dirname(path)] += 1
    return counts
