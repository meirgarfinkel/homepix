"""The library in date order, grouped by month: what the timeline shows."""

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

    @property
    def year(self) -> int:
        return int(self.key[:4])

    @property
    def month(self) -> int:
        return int(self.key[5:7])


def load_items(db, where: str = "1=1", params=()) -> list[Item]:
    """Every showable item, newest first. Photos that couldn't be read have no thumbnail to show."""
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
