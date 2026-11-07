"""The year scrubber: years evenly spaced, short lines filling the gaps, and the bar <-> photos mapping."""

from homepix.ui.views.timeline import from_bar, scrubber_marks, to_bar


def test_years_are_evenly_spaced():
    labels, _ = scrubber_marks(4, 600, line=16)
    assert [k for k, _ in labels] == [0, 1, 2, 3]
    assert [y for _, y in labels] == [0, 150, 300, 450]


def test_few_years_get_many_lines_and_many_years_get_few():
    _, few = scrubber_marks(2, 700, line=16)
    _, many = scrubber_marks(20, 700, line=16)
    assert len(few) > 30
    assert len(many) < len(few)


def test_lines_never_touch_labels_or_crowd_each_other():
    for n in (1, 2, 5, 12, 30):
        labels, ticks = scrubber_marks(n, 700, line=16)
        tops = [y for _, y in labels]
        for t in ticks:
            assert not any(y <= t <= y + 16 for y in tops)
        assert all(b - a >= 8 for a, b in zip(ticks, ticks[1:], strict=False))
        assert all(0 <= t <= 700 for t in ticks)


def test_too_many_years_to_fit_leaves_some_out_evenly():
    labels, _ = scrubber_marks(60, 300, line=16)
    ks = [k for k, _ in labels]
    steps = {b - a for a, b in zip(ks, ks[1:], strict=False)}
    assert len(steps) == 1 and steps.pop() > 1
    ys = [y for _, y in labels]
    assert all(b - a >= 16 for a, b in zip(ys, ys[1:], strict=False))
    assert ys[-1] + 16 <= 300


def test_no_years():
    assert scrubber_marks(0, 600, 16) == ([], [])


def test_each_year_takes_the_same_length_of_bar():
    starts = [0.0, 1000.0, 1100.0]  # a big year, then two small ones
    assert to_bar(starts, 1200, 300, 0) == 0
    assert to_bar(starts, 1200, 300, 500) == 50  # halfway through the first year
    assert to_bar(starts, 1200, 300, 1000) == 100
    assert to_bar(starts, 1200, 300, 1050) == 150
    assert to_bar(starts, 1200, 300, 1200) == 300


def test_bar_and_photos_map_back_and_forth():
    starts = [0.0, 1000.0, 1100.0]
    for c in (0, 250, 999, 1000, 1050, 1199):
        assert abs(from_bar(starts, 1200, 300, to_bar(starts, 1200, 300, c)) - c) < 1e-6
