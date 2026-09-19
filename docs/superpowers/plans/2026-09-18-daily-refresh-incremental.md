# Incremental Daily Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the 8-hour nightly from full rebuilds to diffs in each org DB, with the same dashboard and API numbers, including unused stored columns (`wm_accesses`, `nwm_accesses`, `dailyImageUsage`) and recommender inputs.

**Architecture:** Keep 170 org databases and the public API. Add watermarks and stats columns in each org DB. One `daily.py` coordinator: parallel Commons snapshots (`etl.js`, crash-safe, fingerprint skip), then one dump pass that `COPY`s into Postgres and updates only touched files’ stats. No Redis/Celery/job-queue table. Usages still read the full replica set for our files (Wikimedia `globalimagelinks` has no changelog); we only cheapen **writes** via set-diff.

**Tech Stack:** existing Python 3 + `psycopg2`, Node `etl.js` + `mariadb`, Postgres 12, pytest (add to `services/requirements.txt`).

## Global Constraints

- Same numbers on dashboard, JSON/CSV/PDF, `wm_accesses`, `nwm_accesses`, `dailyImageUsage`, and recommender `is_alive` picture.
- Do not merge the 170 org DBs. Do not `ALTER` Wikimedia `commonswiki_p`.
- No new runtime dependencies except `pytest` (dev/test). No job-queue product.
- `visualizations` PK stays `(media_id, access_date)`. Dump columns stay `[2]=accesses`, `[22]=wm_accesses`, `[23]=nwm_accesses`.
- Usages replica read stays full-set; do not pretend `gil` has `updated_at`.
- First complete NARA/DPLoA snapshot is a long run of the **same** snapshot code (pagination), not a second pipeline.
- Capture `etl.js` stderr. `finalize(true)` must `process.exit(65)` so Python can mark `failed`.
- Do not `DELETE FROM categories` or `is_alive=false` until a snapshot succeeds.
- App queries keep using names `visualizations_sum` and `visualizations_stats` (views over new storage).

## File map

| File | Responsibility |
|---|---|
| `services/requirements.txt` | Add `pytest` |
| `services/etl/SQL/incremental.sql` | Org-DB migration: stats columns, `snapshot_state`, replace matviews with table+views, `gil_page`, `categories.last_crawled_at` |
| `services/etl/migrate_orgs.py` | Apply `incremental.sql` to every catalog GLAM database |
| `services/etl/views.py` | Dump → temp table → `COPY`/`INSERT` visualizations; upsert sum; recompute stats for touched `media_id`s |
| `services/etl/usages_diff.py` | Pure set-diff of gil keys (inserts vs gone) |
| `services/etl/etl.js` | Empty-category exit 65; no premature writes; paginated image SELECT; fingerprint skip; stop holding all usages in one array |
| `services/etl/etl_glam.py` | Log stderr; honor exit 65; `ProcessPoolExecutor` snapshots |
| `services/etl/glams_table.py` | Delete `REFRESH MATERIALIZED VIEW` helpers |
| `services/daily.py` | Coordinator: parallel snapshot → `views.load_yesterday` |
| `services/fill_gaps.py` | Call `views.load_date` instead of `dailyinsert_from_file` + refresh |
| `services/etl/setup.js` | Append `incremental.sql` to the SQL file list |
| `services/test/test_views.py` | Stats math, skip-if-loaded, dump key parse |
| `services/test/test_usages_diff.py` | gil key set-diff |
| `services/test/test_fingerprint.py` | Category fingerprint skip |
| `README.md` | Ops: carnavalize ASCII title; LSoEaPS media category |

Do **not** create a worker framework, a catalog jobs table, or a Python rewrite of the Commons BFS.

---

### Task 1: pytest + stats math (no DB)

**Files:**
- Modify: `services/requirements.txt`
- Create: `services/etl/views.py` (helpers only)
- Test: `services/test/test_views.py`

**Interfaces:**
- Consumes: nothing
- Produces: `percentile_cont(values: list[float], p: float) -> float` matching Postgres `PERCENTILE_CONT` (linear interpolation); `stats_row(accesses: list[int]) -> tuple[int, int, float, float]` as `(tot, n_days, avg, median)`

- [ ] **Step 1: Add pytest**

Append to `services/requirements.txt`:

```
pytest
```

- [ ] **Step 2: Write the failing test**

```python
# services/test/test_views.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services && ENV=production pytest test/test_views.py -v`

(`ENV` is required today by `services/config/__init__.py` if anything imports config. `views.py` helpers must **not** import `config` at module level.)

Expected: FAIL `ModuleNotFoundError: etl.views`

- [ ] **Step 4: Minimal implementation**

```python
# services/etl/views.py
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
```

- [ ] **Step 5: Run tests**

Run: `cd services && pytest test/test_views.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/requirements.txt services/etl/views.py services/test/test_views.py
git commit -m "$(cat <<'EOF'
test: add pytest and visualization percentile helper

EOF
)"
```

---

### Task 2: Org-DB schema (replace matviews)

**Files:**
- Create: `services/etl/SQL/incremental.sql`
- Modify: `services/etl/setup.js` (add `"incremental.sql"` after `maintenance.sql`)
- Create: `services/etl/migrate_orgs.py`
- Modify: `services/etl/SQL/db_init.sql` so **new** GLAMs get the same end state (keep `CREATE TABLE` for core tables; do not create the old matviews — call incremental.sql from setup instead)

**Interfaces:**
- Consumes: catalog `glams.database`
- Produces: per org DB:
  - `images.tot bigint NOT NULL DEFAULT 0`
  - `images.n_days int NOT NULL DEFAULT 0`
  - `images.avg double precision`
  - `images.median double precision`
  - `categories.last_crawled_at timestamptz`
  - `usages.gil_page int`
  - `snapshot_state(views_loaded_for date, tree_crawled_at timestamptz)`
  - table `visualizations_sum_t(access_date date PRIMARY KEY, accesses_sum bigint)`
  - `DROP MATERIALIZED VIEW IF EXISTS visualizations_stats`
  - `DROP MATERIALIZED VIEW IF EXISTS visualizations_sum`
  - `CREATE VIEW visualizations_stats AS SELECT img_name, tot, avg, median FROM images WHERE COALESCE(is_alive, false) AND n_days > 0`
  - `CREATE VIEW visualizations_sum AS SELECT accesses_sum, access_date FROM visualizations_sum_t`

`n_days > 0` keeps files with no visualization rows out of the sidebar (same as the old join).

- [ ] **Step 1: Write SQL**

```sql
-- services/etl/SQL/incremental.sql
ALTER TABLE images ADD COLUMN IF NOT EXISTS tot bigint NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN IF NOT EXISTS n_days int NOT NULL DEFAULT 0;
ALTER TABLE images ADD COLUMN IF NOT EXISTS avg double precision;
ALTER TABLE images ADD COLUMN IF NOT EXISTS median double precision;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS last_crawled_at timestamptz;
ALTER TABLE usages ADD COLUMN IF NOT EXISTS gil_page int;

CREATE TABLE IF NOT EXISTS snapshot_state (
  views_loaded_for date,
  tree_crawled_at timestamptz
);

CREATE TABLE IF NOT EXISTS visualizations_sum_t (
  access_date date PRIMARY KEY,
  accesses_sum bigint NOT NULL
);

DROP MATERIALIZED VIEW IF EXISTS visualizations_stats;
DROP MATERIALIZED VIEW IF EXISTS visualizations_sum;

CREATE OR REPLACE VIEW visualizations_stats AS
  SELECT img_name, tot, avg, median
  FROM images
  WHERE COALESCE(is_alive, false) = true AND n_days > 0;

CREATE OR REPLACE VIEW visualizations_sum AS
  SELECT accesses_sum, access_date FROM visualizations_sum_t;
```

Backfill after alter (same file, after view create):

```sql
INSERT INTO visualizations_sum_t (access_date, accesses_sum)
SELECT access_date, SUM(accesses)
FROM visualizations
GROUP BY access_date
ON CONFLICT (access_date) DO UPDATE SET accesses_sum = EXCLUDED.accesses_sum;

UPDATE images i SET
  tot = s.tot,
  n_days = s.n_days,
  avg = s.avg,
  median = s.median
FROM (
  SELECT media_id,
         SUM(accesses) AS tot,
         COUNT(*) AS n_days,
         AVG(accesses) AS avg,
         PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY accesses) AS median
  FROM visualizations
  GROUP BY media_id
) s
WHERE i.media_id = s.media_id;
```

ponytail: this backfill is O(history) **once per org**. That is the one-time cost that replaces nightly 4h `REFRESH`. Run migrate per-GLAM; NARA/MET will take a while — log and continue.

- [ ] **Step 2: setup.js**

In `services/etl/setup.js`, change the files list to:

```javascript
var files = ["db_init.sql", "dailyInsert.sql", "functions.sql", "maintenance.sql", "incremental.sql"];
```

In `db_init.sql`, **delete** the two `create materialized view` blocks so new DBs are not created with objects `incremental.sql` immediately drops.

- [ ] **Step 3: migrate_orgs.py**

```python
# services/etl/migrate_orgs.py
import pathlib
from etl.glams_table import get_active_glams, get_glam_database_connection

SQL = pathlib.Path(__file__).with_name("SQL").joinpath("incremental.sql").read_text()

def main():
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            conn.cursor().execute(SQL)
            print("migrated", glam["name"])
        finally:
            conn.close()

if __name__ == "__main__":
    main()
```

psycopg2 `execute` may not run multiple statements depending on driver. Split on `;\n` and execute each non-empty statement, or use `psycopg2.extras.execute_batch` — **use a loop over statements split by `;` that are not empty**.

- [ ] **Step 4: Smoke on docker postgres**

Run: `docker-compose up -d` then point a throwaway DB at `incremental.sql` after `db_init.sql`.

Expected: views `visualizations_stats` and `visualizations_sum` exist; no materialized views.

- [ ] **Step 5: Commit**

```bash
git add services/etl/SQL/incremental.sql services/etl/SQL/db_init.sql services/etl/setup.js services/etl/migrate_orgs.py
git commit -m "$(cat <<'EOF'
feat: replace nightly matviews with org-db stats columns

EOF
)"
```

---

### Task 3: Load yesterday’s views without Python name-sets or `dailyinsert()` strings

**Files:**
- Modify: `services/etl/views.py`
- Modify: `services/test/test_views.py`
- Modify: `services/etl/mediacounts_dump.py` (leave `get_file_key_from_line` or move it into `views.py` and delete the old insert path from `daily.py`)
- Modify: `services/etl/glams_table.py` — delete `refresh_glams_visualizations`, `load_glams_images` callers from daily path (functions may remain unused until deleted)

**Interfaces:**
- Consumes: `get_file_key_from_line(line) -> (filename, accesses, wm_accesses, nwm_accesses)`
- Produces:
  - `parse_dump_line(line: bytes) -> tuple[str, int, int, int] | None`
  - `load_views_for_date(glams, filepath, date_val)` → for each glam: skip if `snapshot_state.views_loaded_for >= date_val`; else stage dump keys, join `images`, insert visualizations, `refresh_stats(conn, media_ids)`, upsert sum for `date_val`, set `views_loaded_for`

- [ ] **Step 1: Failing tests for dump parse and skip**

```python
from datetime import date
from etl.views import parse_dump_line, should_skip_views


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
```

- [ ] **Step 2: Run pytest, expect FAIL**

Run: `cd services && pytest test/test_views.py -v`

- [ ] **Step 3: Implement parse + skip + load**

Move key parse to `views.py` (stdlib `urllib.parse.unquote` like `mediacounts_dump.py`).

`load_views_for_date` algorithm (one dump scan, all glams):

1. `COPY` dump lines into **catalog** temp? No — one scan in Python writing a **single** staging file or use `COPY FROM STDIN` into each org’s `tmp_dump(keys)` only for matching names is the old per-line 170-set loop.

   Ponytail that still scales: **one** Python pass builds `filename -> (tot, wm, nwm)`. Then for each glam:

   ```sql
   CREATE TEMP TABLE dump_day (img_name text PRIMARY KEY, accesses bigint, wm bigint, nwm bigint);
   -- COPY stdin
   INSERT INTO visualizations (media_id, access_date, accesses, wm_accesses, nwm_accesses)
   SELECT i.media_id, %s, d.accesses, d.wm, d.nwm
   FROM dump_day d
   JOIN images i ON i.img_name = d.img_name
   ON CONFLICT DO NOTHING;
   ```

   A dict of the whole Commons dump may not fit RAM. **Do not load the whole dump into a dict.**

   Keep a **streaming** pass: for each dump line, `COPY` is wrong per line. Use the existing “filename in set” idea but **one global dict `img_name -> [glam_index]`** built from SQL `SELECT img_name, ...` per glam (still 17M names, ~RAM of today). Ponytail improvement over 170 lookups/line: invert to one map.

   Then batch INSERT (1000 rows) `SELECT dailyinsert(...)` **or** `execute_values` into `visualizations`. Drop `dailyinsert()` for this path.

   After all inserts for a glam: 

   ```sql
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
   WHERE i.media_id = s.media_id;
   ```

   That still recomputes history for every file that has **any** row that day. Files not in yesterday’s dump are untouched. Correct.

   Upsert sum:

   ```sql
   INSERT INTO visualizations_sum_t (access_date, accesses_sum)
   SELECT %s, COALESCE(SUM(accesses), 0) FROM visualizations WHERE access_date = %s
   ON CONFLICT (access_date) DO UPDATE SET accesses_sum = EXCLUDED.accesses_sum;
   ```

   Ensure one row in `snapshot_state` (insert if empty).

- [ ] **Step 4: pytest parse/skip PASS**

Run: `cd services && pytest test/test_views.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/etl/views.py services/test/test_views.py
git commit -m "$(cat <<'EOF'
feat: load one dump day into visualizations and update only touched stats

EOF
)"
```

---

### Task 4: Wire `daily.py`; delete matview refresh from the night

**Files:**
- Modify: `services/daily.py`
- Modify: `services/fill_gaps.py`
- Modify: `services/etl/glams_table.py` (stop calling `REFRESH MATERIALIZED VIEW` from daily; keep functions deleted or stub-raise)

**Interfaces:**
- Consumes: `etl.views.load_views_for_date(glams, filepath, date_val)`
- Produces: nightly path with no `refresh_glams_visualizations`

- [ ] **Step 1: Change `daily.main`**

Replace the block after `process_glams`:

```python
from etl.views import load_views_for_date
from etl.download_mediacounts import get_nfs_file_path
from etl.glams_table import open_glams_connections, close_glams_connections

filepath = get_nfs_file_path(date_val)
open_glams_connections(glams)
try:
    load_views_for_date(glams, filepath, date_val)
finally:
    close_glams_connections(glams)
```

Remove `load_glams_images`, `dailyinsert_from_file`, `refresh_glams_visualizations`. Do **not** wrap in bare `except`.

`fill_gaps.py`: each date call `load_views_for_date` instead of `dailyinsert_from_file`; remove the final `refresh_glams_visualizations`.

- [ ] **Step 2: Delete refresh helpers** from `glams_table.py` (`_refresh_vis_sum_view`, `_refresh_vis_stats_view`, `refresh_glams_visualizations`) and any remaining imports.

- [ ] **Step 3: Commit**

```bash
git add services/daily.py services/fill_gaps.py services/etl/glams_table.py
git commit -m "$(cat <<'EOF'
feat: drop nightly REFRESH MATERIALIZED VIEW

EOF
)"
```

This is the 4-hour win once Task 2 has been migrated in production.

---

### Task 5: Snapshot crash-safety and honest failures

**Files:**
- Modify: `services/etl/etl.js`
- Modify: `services/etl/etl_glam.py`

**Interfaces:**
- Consumes: existing `wikiOpen` / `finalize`
- Produces: empty replica category → `process.exit(65)`; limit exceeded → `process.exit(65)`; Python logs `e.stderr` and `e.returncode`; `update_to_failed` on 65; **no** `delete from categories` until images+usages finished

- [ ] **Step 1: `wikiOpen` empty check**

After `wikiCaller.query(temp_query)`:

```javascript
if (!rows || rows.length === 0) {
  log("Category missing on replica: " + starting_cat);
  process.exit(65);
}
```

- [ ] **Step 2: `finalize(true)` exits 65**

```javascript
async function finalize(failure) {
  log("Number of categories: " + catFreeTail);
  log("Number of images: " + countImages);
  if (failure === true) {
    process.exit(65);
  }
  await glam.connection.query("select * from doMaintenance();");
}
```

- [ ] **Step 3: Move category writes after usages succeed**

Today `afterCategories()` runs `delete from categories; update images set is_alive=false` then `addCategory`. **Delete that write.** Keep the in-memory `catQueue`. After `afterUsages` DB writes succeed, in `finalize(false)`:

1. `BEGIN`
2. `UPDATE images SET is_alive=false` (still a full flip — Task 6 removes it)
3. apply `addCategory` / images already written? Images are written during `loadImagesIntoDB` **before** usages. Split:

**Ponytail order that cannot half-wipe:**

- Load cats/images/usages **in memory or staging tables**
- One transaction: replace categories, upsert images, usages, `doMaintenance`

Minimal change that stops NARA corruption: **remove** `delete from categories; update images set is_alive=false` from `afterCategories`. Write categories with `addCategory` without deleting first only if we fingerprint in Task 6. For this task: buffer category SQL and run it in `finalize(false)` **together with** `doMaintenance`, and **do not** set `is_alive=false` on the whole table. New files still `addImage`. Stale alive files wait for Task 6. ponytail: stale-alive until Task 6 is better than nightly wipe + crash.

So Task 5 only: no `is_alive=false` all; no `delete from categories`; missing cat / limits → exit 65; stderr logged.

- [ ] **Step 4: `etl_glam.py`**

```python
def _etl(name):
    r = subprocess.run(
        ['node', '--max-old-space-size=5120', f'{__package__}/etl.js', name],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.stdout:
        logging.info(r.stdout[-4000:])
    if r.returncode != 0:
        logging.error('etl.js stderr: %s', r.stderr[-8000:] if r.stderr else '')
        logging.error('etl.js returncode %s', r.returncode)
        err = subprocess.CalledProcessError(r.returncode, r.args)
        err.stderr = r.stderr
        raise err
```

Keep `returncode == 65` → `update_to_failed`.

- [ ] **Step 5: Commit**

```bash
git add services/etl/etl.js services/etl/etl_glam.py
git commit -m "$(cat <<'EOF'
fix: stop wiping GLAM tables before etl.js finishes

EOF
)"
```

---

### Task 6: Category fingerprints (skip unchanged file lists)

**Files:**
- Create: `services/test/test_fingerprint.py`
- Modify: `services/etl/etl.js` (compare replica `cat_files`/`cat_subcats` to Postgres `categories`)

**Interfaces:**
- Consumes: `categories.cat_files`, `categories.cat_subcats`
- Produces: `fingerprint_matches(stored_files, stored_subcats, replica_files, replica_subcats) -> bool`; if true, do not `SELECT` files for that category

- [ ] **Step 1: Failing test**

```python
# services/test/test_fingerprint.py
from etl.fingerprint import fingerprint_matches


def test_match():
    assert fingerprint_matches(10, 2, 10, 2) is True


def test_file_count_change():
    assert fingerprint_matches(10, 2, 11, 2) is False


def test_missing_stored():
    assert fingerprint_matches(None, None, 1, 0) is False
```

- [ ] **Step 2: `services/etl/fingerprint.py`**

```python
def fingerprint_matches(stored_files, stored_subcats, replica_files, replica_subcats):
    if stored_files is None or stored_subcats is None:
        return False
    return stored_files == replica_files and stored_subcats == replica_subcats
```

etl.js cannot import Python. **Duplicate the four-line predicate in JS** (same names in a comment `// keep in sync with etl/fingerprint.py`). Plan implementer: JS in `loadImages` before `buildImageQuery`:

```javascript
const prev = await glam.connection.query(
  "SELECT cat_files, cat_subcats FROM categories WHERE page_title = $1",
  [catQueue[catHead].page_title]
);
if (
  prev.rows[0] &&
  prev.rows[0].cat_files === catQueue[catHead].cat_files &&
  prev.rows[0].cat_subcats === catQueue[catHead].cat_subcats
) {
  catHead++;
  return loadImages();
}
```

Use the in-memory BFS `cat_files` already filled from replica in `wikiOpen`/`getLevelChilds`. After a successful file load, `UPDATE categories SET last_crawled_at = NOW()` (and keep cat_files from `addCategory`).

New categories (no row): crawl. Removed categories: Task 5 skipped delete-all; **this task** after BFS: `DELETE FROM categories WHERE page_title <> ALL($1::varchar[])` with the BFS title list. Files whose `cl_to` only pointed at deleted titles: `UPDATE images SET cl_to = array_remove(...)` — **ponytail v1:** `UPDATE images SET is_alive = false WHERE NOT (cl_to && (SELECT array_agg(page_title) FROM categories))` after category delete. One statement.

- [ ] **Step 3: pytest**

Run: `cd services && pytest test/test_fingerprint.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add services/etl/fingerprint.py services/test/test_fingerprint.py services/etl/etl.js
git commit -m "$(cat <<'EOF'
feat: skip Commons file crawl when category counts are unchanged

EOF
)"
```

---

### Task 7: Millions of files — paginate; drop fleet 1000 crash

**Files:**
- Modify: `services/etl/etl.js`
- Modify: `services/config/config.sample.json` (comment only, or delete `limits.categories` as a hard fail)

**Interfaces:**
- Consumes: `config.limits.images` as a **log warning**, not `process.exit(65)`, unless `glams.max_categories` is set later — **ponytail: no new catalog column.** Remove `if (catFreeTail >= config.limits["categories"]) finalize(true)`.
- Produces: `buildImageQuery` with `LIMIT 10000 OFFSET n` loop until a short page.

- [ ] **Step 1: Paginate `loadImages` for one category**

```javascript
var IMAGE_PAGE = 10000;

async function loadImagesForCategory(title) {
  let offset = 0;
  for (;;) {
    const rows = await wikiCaller.query(
      buildImageQuery("'" + title.replace(/'/g, "''") + "'") +
        " LIMIT " + IMAGE_PAGE + " OFFSET " + offset
    );
    // existing row → images[] → loadImagesIntoDB per page (flush each page)
    if (rows.length < IMAGE_PAGE) break;
    offset += IMAGE_PAGE;
  }
}
```

Add `ORDER BY img_name` to `buildImageQuery` so OFFSET is stable.

- [ ] **Step 2: Remove category-cap `finalize(true)` block** (the `catFreeTail >= config.limits["categories"]` one). Keep logging `catFreeTail`.

- [ ] **Step 3: Flush usages every N rows** in `loadUsages` / `afterUsages` instead of one `usages[]` for the whole tree (batch 5000 `addUsage` calls). Prevents 5 GB heap on NARA.

- [ ] **Step 4: Commit**

```bash
git add services/etl/etl.js
git commit -m "$(cat <<'EOF'
feat: stream Commons file and usage pages instead of capping at 1000 cats

EOF
)"
```

Ops (not code): set carnavalize `category` to `Wiki Movimento Brasil's initiative with Carnavalize` (ASCII `'`). Set LSoEaPS to `Images from the London School of Economics`. Document in README under a short “Nightly / large GLAMs” heading.

---

### Task 8: Parallel snapshots

**Files:**
- Modify: `services/etl/etl_glam.py` or `services/daily.py`

**Interfaces:**
- Consumes: `process_glam`
- Produces: `process_glams` uses `ProcessPoolExecutor` (same pattern as `fill_gaps.py`). Default workers: `min(8, os.cpu_count() or 1)`.

- [ ] **Step 1: Parallelize**

```python
from concurrent.futures import ProcessPoolExecutor, as_completed
import os

def process_glams(glams):
    n = min(8, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(process_glam, g): g["name"] for g in glams}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
            except Exception:
                logging.exception("snapshot failed %s", name)
```

`process_glam` must be pickleable: it already only uses `glam['name']` for `_etl`. Pass `dict(name=..., lastrun=..., ...)` only, not `conn`.

Do not start views load until all snapshot futures finish (is_alive should settle before dump join).

- [ ] **Step 2: Commit**

```bash
git add services/daily.py services/etl/etl_glam.py
git commit -m "$(cat <<'EOF'
feat: run Commons snapshots in parallel

EOF
)"
```

---

### Task 9: Usages set-diff writes (`gil_page`)

**Files:**
- Create: `services/etl/usages_diff.py`
- Test: `services/test/test_usages_diff.py`
- Modify: `services/etl/etl.js` `buildUsageQuery` to also `SELECT gil_page`; batch write via Python **or** stay in JS.

Ponytail: keep gil fetch in `etl.js` but write through SQL:

```sql
-- live key is (gil_to, gil_wiki, gil_page)
```

**Interfaces:**
- Consumes: replica rows `{gil_wiki, gil_page, gil_page_title, gil_to}`
- Produces: `diff_usage(ours: set[tuple], replica: set[tuple]) -> (to_insert, to_close)` where tuple is `(gil_to, gil_wiki, gil_page)`

- [ ] **Step 1: Failing test**

```python
from etl.usages_diff import diff_usage

def test_insert_and_close():
    ours = {("A.jpg", "enwiki", 1), ("A.jpg", "enwiki", 2)}
    replica = {("A.jpg", "enwiki", 2), ("A.jpg", "enwiki", 3)}
    ins, close = diff_usage(ours, replica)
    assert ins == {("A.jpg", "enwiki", 3)}
    assert close == {("A.jpg", "enwiki", 1)}
```

- [ ] **Step 2: Implement**

```python
def diff_usage(ours, replica):
    return replica - ours, ours - replica
```

- [ ] **Step 3: Wire writes in etl.js after fetching usages**

Load current live keys:

`SELECT gil_to, gil_wiki, gil_page FROM usages WHERE is_alive = true AND gil_page IS NOT NULL`

If `gil_page` is null (pre-migration rows), fall back to one full `addUsage` pass (old function) for that glam until backfilled. Backfill: set `gil_page` from replica on first successful fetch.

`to_insert`: `addUsage` + `gil_page`.  
`to_close`: `UPDATE usages SET is_alive=false WHERE ...` (do **not** wait 5 days for keys we know vanished). Keep `doMaintenance` 5-day rule for keys not in the replica result only if we still full-scan — we do full-scan, so set-diff **is** exact and 5-day lag is obsolete for scanned files. **Keep `doMaintenance`** for `dailyImageUsage` insert; change the `is_alive=false WHERE last_seen < current_date-5` to run only if we did **not** set-diff. If set-diff ran, skip that update.

- [ ] **Step 4: pytest**

Run: `cd services && pytest test/test_usages_diff.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/etl/usages_diff.py services/test/test_usages_diff.py services/etl/etl.js services/etl/SQL/functions.sql
git commit -m "$(cat <<'EOF'
feat: apply Commons usage as a set-diff on gil_page keys

EOF
)"
```

---

### Task 10: README ops + invert dump map

**Files:**
- Modify: `README.md`
- Modify: `services/etl/views.py` if Task 3 still loops 170 sets per dump line

**Interfaces:** none new

- [ ] **Step 1: README**

Add under installation/services:

- Migrate org DBs: `ENV=production python -m etl.migrate_orgs` from `services/`
- Carnavalize: category must use ASCII apostrophe `Wiki Movimento Brasil's initiative with Carnavalize`
- LSoEaPS: use `Images from the London School of Economics`, not the topic tree
- NARA/DPLoA: first snapshot after pagination can run for hours; other GLAMs already parallel
- Nightly no longer `REFRESH MATERIALIZED VIEW`

- [ ] **Step 2: Dump index**

When loading names from each glam, build `name_to_glams: dict[str, list[int]]` once. Each dump line: `for gi in name_to_glams.get(key, ())`.

- [ ] **Step 3: Commit**

```bash
git add README.md services/etl/views.py
git commit -m "$(cat <<'EOF'
docs: nightly migrate and GLAM category ops

EOF
)"
```

---

## Self-review

**Spec coverage**
- Incremental stats / drop 4h REFRESH → Tasks 2–4  
- COPY/join dump, no 170-set inner loop → Tasks 3, 10  
- Org-DB watermarks `snapshot_state` → Task 2, 3  
- Category fingerprints → Task 6  
- Async snapshots without a queue product → Task 8  
- >1000 cats, millions of files (paginate, no fleet cap) → Task 7  
- NARA/DPLoA same path, long first crawl → Task 7 + README  
- Carnavalize ASCII / LSoEaPS retarget → Task 10  
- etl.js stderr, exit 65, no pre-crash wipe → Task 5  
- Usages: no replica `changed_since`; `gil_page` set-diff → Task 9  
- fill_gaps uses new views load → Task 4  
- API names preserved via views → Task 2  
- `wm_accesses`/`nwm_accesses` still inserted → Task 3  
- `dailyImageUsage` via `doMaintenance` → Task 5/9  
- Recommender still reads `is_alive` → unchanged API tables  

**Placeholders:** none intended. Replica `cl_timestamp` not used (not required).

**Types:** `stats_row` → `(tot, n_days, avg, median)`; `diff_usage` → `(set, set)` of `(gil_to, gil_wiki, gil_page)`; `load_views_for_date(glams, filepath, date_val)`.

**Out of scope (ponytail):** Celery, merging DBs, ALTER on `commonswiki_p`, Python rewrite of BFS, weekly usage-only reconcile (nightly still full gil **read**).
