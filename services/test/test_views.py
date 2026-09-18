from datetime import date
from etl.views import (
    load_views_for_date,
    parse_dump_line,
    percentile_cont,
    recompute_all_stats,
    should_skip_views,
    stats_row,
)


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


def _tsv_line(filename, tot, wm, nwm):
    cols = [""] * 24
    cols[0] = f"https://example/commons/a/ab/{filename}"
    cols[2] = str(tot)
    cols[22] = str(wm)
    cols[23] = str(nwm)
    return "\t".join(cols) + "\n"


def _sql(sql):
    return sql.decode() if isinstance(sql, bytes) else sql


def _norm(sql):
    return " ".join(_sql(sql).split()).lower()


class FakeConn:
    encoding = "UTF8"

    def __init__(self, loaded_for, images):
        self.loaded_for = loaded_for
        self.images = images
        self.executed = []
        self.bound = []

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.connection = conn
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.conn.executed.append((_sql(sql), params))
        n = _norm(sql)
        if n.startswith("update snapshot_state"):
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchone(self):
        return None if self.conn.loaded_for is None else (self.conn.loaded_for,)

    def __iter__(self):
        return iter(self.conn.images)

    def mogrify(self, query, vars=None):
        self.conn.bound.append(vars)
        return b"(1)"

    def close(self):
        pass


def test_load_views_for_date_fake_conn(tmp_path):
    day = date(2026, 9, 17)
    skip = FakeConn(loaded_for=day, images=[("Skip.jpg", 99)])
    load = FakeConn(loaded_for=date(2026, 9, 16), images=[("Foo_bar.jpg", 7)])
    dump = tmp_path / "day.tsv"
    dump.write_text(_tsv_line("Foo_bar.jpg", 9, 4, 5) + _tsv_line("Skip.jpg", 1, 0, 1))

    load_views_for_date([{"conn": skip}, {"conn": load}], str(dump), day)

    assert skip.bound == []
    assert not any("from images" in _norm(s) for s, _ in skip.executed)

    assert load.bound == [(7, day, 9, 4, 5)]
    sqls = [_norm(s) for s, _ in load.executed]
    assert any("select img_name" in s and "from images" in s for s in sqls)
    assert any("insert into visualizations" in s and "on conflict do nothing" in s for s in sqls)

    refresh = [(s, p) for s, p in load.executed if "percentile_cont" in _norm(s)]
    assert len(refresh) == 1
    rsql, rparams = refresh[0]
    assert "with touched as" in _norm(rsql)
    assert "where access_date" in _norm(rsql)
    assert "any(" not in _norm(rsql)
    assert rparams == (day,)

    assert any("insert into visualizations_sum_t" in _norm(s) for s, _ in load.executed)
    assert any(
        "greatest(views_loaded_for" in _norm(s) and p == (day,)
        for s, p in load.executed
    )


def test_load_views_for_date_skip_loaded_false(tmp_path):
    day = date(2026, 9, 17)
    already = FakeConn(loaded_for=day, images=[("Foo_bar.jpg", 7)])
    dump = tmp_path / "day.tsv"
    dump.write_text(_tsv_line("Foo_bar.jpg", 9, 4, 5))
    load_views_for_date([{"conn": already}], str(dump), day, skip_loaded=False)
    assert already.bound == [(7, day, 9, 4, 5)]


def test_recompute_all_stats_full_history():
    conn = FakeConn(loaded_for=None, images=[])
    recompute_all_stats(conn)
    sqls = [_norm(s) for s, _ in conn.executed]
    assert any(
        "update images" in s
        and "group by media_id" in s
        and "with touched as" not in s
        for s in sqls
    )
