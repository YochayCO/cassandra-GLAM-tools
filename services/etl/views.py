import bz2
import io
from urllib.parse import unquote

_BATCH = 1000
_INSERT = (
    "INSERT INTO visualizations "
    "(media_id, access_date, accesses, wm_accesses, nwm_accesses) "
    "VALUES %s ON CONFLICT DO NOTHING"
)


def percentile_cont(values, p):
    if not values:
        raise ValueError("empty")
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return float(xs[0])
    idx = (n - 1) * p
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    w = idx - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def stats_row(accesses):
    tot = sum(accesses)
    n = len(accesses)
    return tot, n, tot / n, percentile_cont(accesses, 0.5)


def parse_dump_line(line):
    try:
        cols = line.decode().split("\t")
        name = unquote(cols[0].split("/")[-1])
        return name, int(cols[2]), int(cols[22]), int(cols[23])
    except (LookupError, ValueError, UnicodeDecodeError):
        return None


def should_skip_views(loaded_for, date_val):
    return loaded_for is not None and loaded_for >= date_val


def refresh_stats(conn, date_val):
    cur = conn.cursor()
    cur.execute(
        """
        WITH touched AS (
          SELECT DISTINCT media_id FROM visualizations WHERE access_date = %s
        )
        UPDATE images i SET tot = s.tot, n_days = s.n_days, avg = s.avg, median = s.median
        FROM (
          SELECT v.media_id,
                 SUM(v.accesses) AS tot,
                 COUNT(*) AS n_days,
                 AVG(v.accesses)::float AS avg,
                 PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.accesses) AS median
          FROM visualizations v
          JOIN touched t ON t.media_id = v.media_id
          GROUP BY v.media_id
        ) s
        WHERE i.media_id = s.media_id
        """,
        (date_val,),
    )
    cur.close()


def _iter_dump(filepath):
    with open(filepath, "rb") as f:
        if str(filepath).endswith(".bz2"):
            with bz2.BZ2File(io.BufferedReader(f, buffer_size=16 * 1024 * 1024)) as src:
                yield from src
        else:
            yield from f


def _flush(conn, rows):
    if not rows:
        return
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    execute_values(cur, _INSERT, rows, page_size=_BATCH)
    cur.close()
    rows.clear()


def _upsert_sum(conn, date_val):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO visualizations_sum_t (access_date, accesses_sum)
        SELECT %s, COALESCE(SUM(accesses), 0) FROM visualizations WHERE access_date = %s
        ON CONFLICT (access_date) DO UPDATE SET accesses_sum = EXCLUDED.accesses_sum
        """,
        (date_val, date_val),
    )
    cur.close()


def _set_views_loaded_for(conn, date_val):
    cur = conn.cursor()
    cur.execute("UPDATE snapshot_state SET views_loaded_for = %s", (date_val,))
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO snapshot_state (views_loaded_for) VALUES (%s)", (date_val,)
        )
    cur.close()


def load_views_for_date(glams, filepath, date_val):
    active = []
    # ponytail: inverted name map is the same RAM as today's per-glam sets; COPY dump_day if this dict is the bottleneck
    name_to_targets = {}
    for glam in glams:
        conn = glam.get("conn")
        if conn is None:
            continue
        cur = conn.cursor()
        cur.execute("SELECT views_loaded_for FROM snapshot_state LIMIT 1")
        row = cur.fetchone()
        loaded_for = None if row is None else row[0]
        if should_skip_views(loaded_for, date_val):
            cur.close()
            continue
        cur.execute("SELECT img_name, media_id FROM images")
        gi = len(active)
        for img_name, media_id in cur:
            name_to_targets.setdefault(img_name, []).append((gi, media_id))
        cur.close()
        active.append(glam)
    if not active:
        return
    batches = [[] for _ in active]
    for line in _iter_dump(filepath):
        parsed = parse_dump_line(line)
        if parsed is None:
            continue
        name, tot, wm, nwm = parsed
        for gi, media_id in name_to_targets.get(name, ()):
            batches[gi].append((media_id, date_val, tot, wm, nwm))
            if len(batches[gi]) >= _BATCH:
                _flush(active[gi]["conn"], batches[gi])
    for gi, glam in enumerate(active):
        conn = glam["conn"]
        _flush(conn, batches[gi])
        refresh_stats(conn, date_val)
        _upsert_sum(conn, date_val)
        _set_views_loaded_for(conn, date_val)
