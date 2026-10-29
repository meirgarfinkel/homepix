"""The year scrubber's labels: every year shown, none overlapping, all on screen."""

from homepix.ui.views.timeline import year_labels


def test_every_year_gets_a_label_even_when_close_together():
    # one big year, then ten small ones squeezed into the bottom few pixels
    starts = [(2024, 0, 0.0)] + [(2023 - i, i + 1, 590.0 + i) for i in range(10)]
    out = year_labels(starts, gap=16, bottom=600)
    assert [y for y, _, _ in out] == [s[0] for s in starts]
    ys = [y for _, _, y in out]
    assert all(b - a >= 16 for a, b in zip(ys, ys[1:], strict=False))
    assert ys[0] >= 0 and ys[-1] <= 600


def test_labels_stay_where_their_year_starts_when_there_is_room():
    starts = [(2024, 0, 0.0), (2023, 5, 200.0), (2022, 9, 400.0)]
    assert [y for _, _, y in year_labels(starts, 16, 600)] == [0.0, 200.0, 400.0]


def test_too_many_years_to_fit_leaves_some_out_evenly():
    starts = [(2000 + i, i, i * 2.0) for i in range(60)]
    out = year_labels(starts, gap=16, bottom=300)
    ys = [y for _, _, y in out]
    assert len(out) <= 300 // 16 + 1 and len(out) >= 10
    assert all(b - a >= 16 for a, b in zip(ys, ys[1:], strict=False))


def test_no_years():
    assert year_labels([], 16, 600) == []
