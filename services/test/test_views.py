from datetime import date
from etl.views import parse_dump_line, percentile_cont, should_skip_views, stats_row


def test_percentile_cont_median_odd():
    assert percentile_cont([1, 2, 3], 0.5) == 2


def test_percentile_cont_median_even_interpolates():
    # Postgres PERCENTILE_CONT(0.5) on {1,2,3,4} = 2.5
    assert percentile_cont([1, 2, 3, 4], 0.5) == 2.5


def test_stats_row():
    tot, n, avg, median = stats_row([10, 20, 30])
    assert tot == 60
    assert n == 3
    assert avg == 20
    assert median == 20


def test_parse_dump_line_filename_and_counts():
    # Wikimedia v00: col0 path, col2 total, col22 wm, col23 nwm
    cols = [""] * 24
    cols[0] = "https://example/commons/a/ab/Foo_bar.jpg"
    cols[2] = "9"
    cols[22] = "4"
    cols[23] = "5"
    line = ("\t".join(cols) + "\n").encode()
    name, tot, wm, nwm = parse_dump_line(line)
    assert name == "Foo_bar.jpg"
    assert (tot, wm, nwm) == (9, 4, 5)


def test_should_skip_views_when_already_loaded():
    assert should_skip_views(date(2026, 9, 17), date(2026, 9, 17)) is True
    assert should_skip_views(date(2026, 9, 16), date(2026, 9, 17)) is False
    assert should_skip_views(None, date(2026, 9, 17)) is False
