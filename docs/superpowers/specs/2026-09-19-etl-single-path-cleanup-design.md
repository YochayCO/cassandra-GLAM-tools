# ETL single-path cleanup

**Branch:** `refactor_nightly` (same branch as the incremental nightly; cleanup before merge).  
**Date:** 2026-09-19

## Problem

The incremental nightly on `refactor_nightly` still has two writers for the same facts.

Production is `etl.js` plus `functions.sql`. Beside it sit Python modules the crawler never imports, a second `AddCategory` file migrate runs and then overwrites, a plpgsql splitter that exists only because psycopg2 cannot `execute()` a whole `$$` script, and pytest that greps `etl.js` source (`_fn()`). `finalize` blanks `categories.cl_to` / `cat_level` then replays `addCategory()` appends, so a crash between those steps leaves empty parent arrays. `usageSetDiffRan` is a dual path for live `gil_page IS NULL` rows.

Dashboard and API numbers must stay the same. This is not a new ETL.

## Goals

- One source for category parent arrays, usage close, and SQL functions.
- Crash during `finalize` must not leave empty `cl_to` / `cat_level`.
- Preserve `usages.first_seen` across the `gil_page` cutover.
- Delete the toys and the tests that only freeze source shape.

## Non-goals

- API, dump/`views.py` math, watermarks, replica SQL, parallel snapshots, `AddImage` membership logic.
- Rewriting `etl.js` off Node, merging org DBs, adding dependencies.
- Filling `gil_page` in migrate (no replica there).
- Closing or re-inserting usages in migrate (that would reset `first_seen`).

## Current vs target

| Today | Target |
|---|---|
| `fingerprint.py` / `usages_diff.py` unused at runtime | Delete them |
| `add_category.sql` then `functions.sql` overwrite | Only `functions.sql`; drop `AddCategory` |
| `sql_statements` regex splitter + per-stmt `execute` | Whole files through node-pg `query(file)` (same as `setup.js`) |
| `_fn()` greps of `etl.js` | `node --test` on exported pure helpers; pytest for Python wiring |
| `finalize`: blank arrays → string of `addCategory()` → delete orphans | One replace upsert of grouped parents, then delete, in a transaction |
| `usageSetDiffRan` + 5-day close in `finalize` | Always set-diff; leftover `gil_page IS NULL` close after `addUsage`; no flag |

## SQL apply

`psycopg2.cursor.execute()` cannot run a multi-statement / `$$` file (`can't execute multiple commands at once`). `setup.js` already runs each SQL file as one `db.query(entireFile)` via node-pg.

**Helper:** export `runSqlFiles(db, files)` from `services/etl/setup.js`. Read `services/etl/SQL/<file>` and `await db.query(sql)` in order. Reject on error.

**`setup.js` CLI** (`node setup.js <GlamName>`) stays the new-org path: `db_init.sql`, `dailyInsert.sql`, `functions.sql`, `maintenance.sql`, `incremental.sql`. Guard the CLI with `require.main === module`. On query error, exit 1 (today it logs and `process.exit(0)`).

**`migrate_orgs.py`** (still `ENV=production python -m etl.migrate_orgs` from `services/`):

1. For each active glam, psycopg2: `drop_view_or_matview` for `visualizations_stats` and `visualizations_sum` (relkind inspect; keep as-is).
2. `subprocess.run([node, Path(__file__).resolve().with_name("setup.js"), "--apply", glam["name"], "incremental.sql", "functions.sql", "maintenance.sql"], check=True)` inheriting `ENV`. `__dirname` in `setup.js` locates `SQL/`; cwd does not matter. Exit 1 from node fails that org.
3. On any org failure: log, continue, `sys.exit(1)` at the end. Close every connection.

Delete `sql_statements`, `apply_sql`, and the in-process load of `add_category.sql`. Do not add `psql`.

## Category parents

`searchCatQueue` only looks at `i < catHead`, so `catQueue[0..catFreeTail)` can hold the same `page_title` more than once with different `.father` / `.level`. That is how multi-parent trees are represented. Do not change the BFS.

**`groupCatParents(queue, n)`** (pure, in `services/etl/etl_logic.js` so tests do not load `config.js` / MariaDB):

- Walk `i in [0, n)`.
- One row per `page_title`: `cat_subcats` / `cat_files` from the last row for that title (replica counts are the category’s, not the edge’s).
- `cl_to` / `cat_level`: append `.father` / `.level` in walk order, including duplicate fathers. Same arrays today’s `||` would build.
- Root stays `father = "ROOT"`, `level = 0`.

**`afterCategories`:** do not build `categorySql`, do not call `addCategory`. Keep the in-memory queue and continue to `loadImages`.

**`finalize`:** drop the unused `failure` argument. Empty-category `process.exit(65)` stays only in `wikiOpen`.

`glam.connection` is a node-pg `Pool`. `BEGIN`/`COMMIT` must use one checked-out client (`connect()` / `release()`). Separate `pool.query` calls can land on different backends.

On the success path, on that client:

1. `BEGIN`
2. If there is at least one grouped row, one query, `$1` = JSON array of `{page_title, cat_subcats, cat_files, cl_to, cat_level}` (`cl_to` strings, `cat_level` numbers):

```sql
INSERT INTO categories (page_title, cat_subcats, cat_files, cl_to, cat_level, last_crawled_at)
SELECT
  e->>'page_title',
  (e->>'cat_subcats')::int,
  (e->>'cat_files')::int,
  ARRAY(SELECT jsonb_array_elements_text(e->'cl_to')),
  ARRAY(SELECT jsonb_array_elements_text(e->'cat_level'))::int[],
  NOW()
FROM jsonb_array_elements($1::jsonb) AS e
ON CONFLICT (page_title) DO UPDATE SET
  cat_subcats = EXCLUDED.cat_subcats,
  cat_files = EXCLUDED.cat_files,
  cl_to = EXCLUDED.cl_to,
  cat_level = EXCLUDED.cat_level,
  last_crawled_at = NOW();
```

   No `||`, no blank-first. `page_title` is already the PK.
3. `DELETE FROM categories WHERE page_title <> ALL($2::varchar[])` with the unique grouped titles, when that list is non-empty.
4. `UPDATE images SET is_alive = false WHERE NOT (cl_to && (SELECT array_agg(page_title) FROM categories))`
5. `SELECT * FROM doMaintenance();`
6. `COMMIT`

On throw: `ROLLBACK`, rethrow, then `release()`. Existing `etl.js` `.catch` still `process.exit(1)`. Always `release()` in `finally`.

Do not put usage writes in this transaction. Images are still `addImage`’d during `loadImages`; that is unchanged.

**`functions.sql`:** `DROP FUNCTION IF EXISTS AddCategory(varchar, int, int, varchar, int);` then keep `AddImage` and `AddUsage`. Delete `services/etl/SQL/add_category.sql`.

## Usages

Do not touch `usages` in migrate.

Always set-diff (no `usageSetDiffRan`, no stale `gil_page IS NULL` probe that disables the set).

**`prepareUsages`:** load `usageOurs` from `usages WHERE is_alive = true AND gil_page IS NOT NULL` using `usageKey(gil_to, gil_wiki, gil_page)` (`"\0"`-joined), same as today.

**`flushUsages`:** if replica `gil_page` is present and the key is in `usageOurs`, delete the key and skip `addUsage`. Otherwise `addUsage` (fills `gil_page` via existing `COALESCE` on a live `(wiki, page_title, to)` row). That is how replica-seen NULL-key rows keep `first_seen`.

**`afterUsages` order:**

1. `flushUsages`
2. `closeVanishedUsages` (always): close remaining keyed triples in `usageOurs`; `UPDATE usages SET last_seen=CURRENT_DATE WHERE is_alive = true AND gil_page IS NOT NULL`
3. `UPDATE usages SET is_alive=false WHERE is_alive = true AND gil_page IS NULL` (vanished unkeyed leftovers; no-op after cutover)
4. `finalize` (category transaction)

Delete the `finalize` 5-day statement (`last_seen < current_date-5`).

`doMaintenance` still groups `usages WHERE last_seen=current_date`, so it must run after the `last_seen` bump. That is already true if bump stays in `closeVanishedUsages` before `finalize`.

## Fingerprint

Leave the four-line count compare inline in `loadImages`. Delete `fingerprint.py` and the `// keep in sync with etl/fingerprint.py` comment. No Python twin.

## Files

| Path | Action |
|---|---|
| `services/etl/fingerprint.py` | Delete |
| `services/etl/usages_diff.py` | Delete |
| `services/etl/SQL/add_category.sql` | Delete |
| `services/etl/SQL/functions.sql` | Drop `AddCategory`; keep `AddImage` / `AddUsage` |
| `services/etl/etl_logic.js` | Create: `groupCatParents`, `usageKey` |
| `services/etl/etl.js` | Use helpers; upsert finalize; always set-diff; `require.main` on the CLI entry |
| `services/etl/setup.js` | Export `runSqlFiles`; `--apply` CLI; `require.main`; exit 1 on SQL error |
| `services/etl/migrate_orgs.py` | Drop splitter; drop views; subprocess `--apply` |
| `services/test/test_etl_logic.js` | Create: `node --test` for `groupCatParents` and `usageKey` set-diff skip/close |
| `services/test/test_fingerprint.py` | Delete with `fingerprint.py` |
| `services/test/test_usages_diff.py` | Delete with `usages_diff.py` |
| `services/test/test_migrate_orgs.py` | Keep `drop_view_or_matview` + `main()` continues and exits 1; drop splitter / `add_category` tests; mock the node subprocess |
| `services/test/test_etl_crash_safety.py` | Keep `etl_glam` subprocess / exit 65 / stderr tests; drop `_fn()` greps |
| `README.md` | Migrate blurb: whole-file apply, exit 1 if any org failed. Remove the “lacks 4-arg `AddUsage`” diagnostic (that was the splitter). |

## Tests

- `cd services && node --test test/test_etl_logic.js` — `groupCatParents` (two fathers → two-element arrays, last counts win); `usageKey` skip removes that key from a `Set`, leftovers are the close set. No DB. The leftover-NULL `UPDATE` stays a one-liner in `etl.js`, not a helper test.
- `cd services && pytest` — migrate view drop + fail-soft `main()`; `etl_glam` exit 65; `views.py`. No `_fn()`, no `sql_statements`.

`AddUsage` / `AddImage` SQL bodies are not grep-tested. Whole-file apply is the contract.

## Errors and cutover

- Category upsert crash: rollback; next successful night replaces arrays from `catQueue` again.
- Usage crash before `finalize`: some keys closed, categories old. Next night set-diff / `addUsage` repairs. Acceptable; the empty-array hole was the category wipe.
- First night after this ships: live NULL `gil_page` rows that still appear on the replica get `gil_page` from `addUsage` and keep `first_seen`. Ones that do not appear are closed by the NULL update, not re-inserted.
- `wikiOpen` missing replica category: still `process.exit(65)` before any of the above.

## Ops

Same commands. Run migrate once after deploy so every org DB drops `AddCategory` and applies current `functions.sql` as a whole file. Do not start cron until every active DB prints `migrated` and the process exits 0.
