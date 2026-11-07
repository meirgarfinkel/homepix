"""The year scrubber: every year gets room for its label, the rest goes by size; short lines fill
the gaps; and the bar <-> photos mapping."""

from homepix.ui.views.timeline import from_bar, scrubber_marks, to_bar, year_bounds


def shares(bounds):
    return [b - a for a, b in zip(bounds, bounds[1:], strict=False)]


def test_same_sized_years_get_the_same_room():
    b = year_bounds([0.0, 100.0, 200.0, 300.0], 400, 600, min_share=44)
    assert b == [0, 150, 300, 450, 600]


def test_a_tiny_year_still_gets_its_minimum_and_a_big_one_gets_the_rest():
    b = year_bounds([0.0, 80000.0], 80010, 600, min_share=44)
    small = shares(b)[1]
    assert 44 <= small < 45
    assert shares(b)[0] > 500


def test_too_many_years_for_the_minimum_share_evenly():
    b = year_bounds([float(i) for i in range(60)], 60 + 5000, 300, min_share=44)
    assert all(abs(s - 5) < 1e-9 for s in shares(b))


def test_few_years_get_many_lines_and_many_years_get_few():
    _, few = scrubber_marks(year_bounds([0.0, 100.0], 200, 700, 44), line=16)
    _, many = scrubber_marks(year_bounds([i * 100.0 for i in range(20)], 2000, 700, 44), line=16)
    assert len(few) > 30
    assert len(many) < len(few)


def test_lines_never_touch_labels_or_crowd_each_other():
    for n in (1, 2, 5, 12, 30):
        labels, ticks = scrubber_marks(year_bounds([i * 10.0 + i * i for i in range(n)], 2000, 700, 44), 16)
        tops = [y for _, y in labels]
        for t in ticks:
            assert not any(y <= t <= y + 16 for y in tops)
        assert all(b - a >= 8 for a, b in zip(ticks, ticks[1:], strict=False))
        assert all(0 <= t <= 700 for t in ticks)


def test_labels_never_overlap_and_crowded_years_are_skipped():
    bounds = year_bounds([float(i) for i in range(60)], 60, 300, 44)
    labels, _ = scrubber_marks(bounds, 16)
    ys = [y for _, y in labels]
    assert 5 < len(labels) < 60
    assert all(b - a >= 16 for a, b in zip(ys, ys[1:], strict=False))
    assert ys[-1] + 16 <= 300


def test_no_years():
    assert scrubber_marks(year_bounds([], 0, 600, 44), 16) == ([], [])


def test_drag_moves_through_each_year_within_its_share():
    starts, total = [0.0, 1000.0, 1100.0], 1200
    b = [0.0, 200.0, 250.0, 300.0]
    assert to_bar(starts, total, b, 500) == 100  # halfway through the first year
    assert to_bar(starts, total, b, 1050) == 225
    assert to_bar(starts, total, b, 1200) == 300
    for c in (0, 250, 999, 1000, 1050, 1199):
        assert abs(from_bar(starts, total, b, to_bar(starts, total, b, c)) - c) < 1e-6
