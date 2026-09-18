from etl.views import percentile_cont, stats_row


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
