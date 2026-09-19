# ETL Single-Path Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One writer for category parents, usage close, and SQL functions on `refactor_nightly`, without empty `cl_to` after a crashed `finalize`, and without resetting `usages.first_seen`.

**Architecture:** Delete the unused Python twins, the extra `AddCategory` file, the plpgsql splitter, and the `_fn()` greps. Group duplicate `catQueue` rows in `etl_logic.js`, replace parent arrays in one Postgres transaction on a checked-out pool client, always set-diff usages, and apply SQL files through the existing `setup.js` CLI (`node setup.js <GlamName> [files…]`).

**Tech Stack:** Node (existing `pg` + `mariadb`), Python 3 + `psycopg2` + `pytest`, Postgres 12. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-etl-single-path-cleanup-design.md`

**Plan wins over spec** (thermo-nuclear judo, same behavior): no `--apply` flag, no `module.exports` on `setup.js`, no `usageKey` module, no `require.main` on `etl.js`, no jsonb unpack. Parameterized `INSERT … ON CONFLICT` loop inside `BEGIN`/`COMMIT`. Optional trailing filenames on `setup.js`; omit them and the new-org default list still runs (`new_glam_listener` must keep working).

## Global Constraints

- Same dashboard and API numbers. Do not merge org DBs. No new runtime dependencies.
- Do not fill, close, or re-insert `usages` in migrate (that resets `first_seen`).
- Do not change replica SQL, `views.py`, watermarks, parallel snapshots, or `AddImage`.
- `glam.connection` is a node-pg `Pool`. `BEGIN`/`COMMIT` on one `connect()`/`release()` client.
- Usage writes stay outside the category transaction. `doMaintenance` runs after the `last_seen` bump.
- `wikiOpen` missing replica category still `process.exit(65)`.
- `last_crawled_at` is stamped only in `loadImages`. Finalize must not write that column.
- `etl.js` stays one crawler file (461 lines; do not split it).
- Delete rather than wrap. Do not add `sql_files.js`, `psql`, or Python twins.
- Worktree may already contain unstaged `drop_view_or_matview`. Keep that function; Task 4’s `migrate_orgs.py` is the full file including it. Do not revert it then re-add it.
- Do not edit `docs/superpowers/plans/2026-09-18-daily-refresh-incremental.md`.

## File map

| File | Responsibility |
|---|---|
| `services/etl/etl_logic.js` | `groupCatParents(queue, n)` only |
| `services/test/test_etl_logic.js` | `node --test` for grouping |
| `services/etl/etl.js` | Crawler; uses `groupCatParents`; set-diff; transactional finalize |
| `services/etl/setup.js` | Whole-file SQL apply; default files or trailing argv files |
| `services/etl/migrate_orgs.py` | Drop views; `subprocess` `setup.js` with three files |
| `services/etl/SQL/functions.sql` | `AddImage` + `AddUsage`; drop `AddCategory` |
| `README.md` | Migrate blurb: `failed` = did not finish, re-run |

Delete: `fingerprint.py`, `usages_diff.py`, `add_category.sql`, `test_fingerprint.py`, `test_usages_diff.py`.

---

### Task 1: `groupCatParents`

**Files:**
- Create: `services/etl/etl_logic.js`
- Test: `services/test/test_etl_logic.js`

**Interfaces:**
- Consumes: nothing
- Produces: `groupCatParents(queue, n) -> Array<{page_title: string, cat_subcats: number, cat_files: number, cl_to: string[], cat_level: number[]}>` — one row per first-seen `page_title`, fathers/levels appended in walk order (including duplicate fathers), `cat_subcats`/`cat_files` from the last row for that title. `n` is `catFreeTail` (not `queue.length`).

- [ ] **Step 1: Write the failing test**

```javascript
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { groupCatParents } = require("../etl/etl_logic.js");

test("two fathers become two-element arrays; last counts win", () => {
  const queue = [
    { page_title: "Root", father: "ROOT", level: 0, cat_subcats: 1, cat_files: 0 },
    { page_title: "Child", father: "Root", level: 1, cat_subcats: 0, cat_files: 4 },
    { page_title: "Child", father: "Other", level: 2, cat_subcats: 0, cat_files: 5 },
  ];
  const rows = groupCatParents(queue, 3);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    page_title: "Root",
    cat_subcats: 1,
    cat_files: 0,
    cl_to: ["ROOT"],
    cat_level: [0],
  });
  assert.deepEqual(rows[1], {
    page_title: "Child",
    cat_subcats: 0,
    cat_files: 5,
    cl_to: ["Root", "Other"],
    cat_level: [1, 2],
  });
});

test("n shorter than queue is the walk length", () => {
  const queue = [
    { page_title: "A", father: "ROOT", level: 0, cat_subcats: 0, cat_files: 1 },
    { page_title: "B", father: "A", level: 1, cat_subcats: 0, cat_files: 2 },
  ];
  const rows = groupCatParents(queue, 1);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].page_title, "A");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services && node --test test/test_etl_logic.js`

Expected: FAIL, cannot find module `../etl/etl_logic.js`

- [ ] **Step 3: Write minimal implementation**

```javascript
function groupCatParents(queue, n) {
  const byTitle = new Map();
  for (let i = 0; i < n; i++) {
    const c = queue[i];
    let row = byTitle.get(c.page_title);
    if (!row) {
      row = {
        page_title: c.page_title,
        cat_subcats: c.cat_subcats,
        cat_files: c.cat_files,
        cl_to: [],
        cat_level: [],
      };
      byTitle.set(c.page_title, row);
    }
    row.cl_to.push(c.father);
    row.cat_level.push(c.level);
    row.cat_subcats = c.cat_subcats;
    row.cat_files = c.cat_files;
  }
  return Array.from(byTitle.values());
}

module.exports = { groupCatParents };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services && node --test test/test_etl_logic.js`

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/etl/etl_logic.js services/test/test_etl_logic.js
git commit -m "$(cat <<'EOF'
feat: group catQueue parents in one JS helper

EOF
)"
```

---

### Task 2: Delete toys and `_fn()` greps

**Files:**
- Delete: `services/etl/fingerprint.py`, `services/etl/usages_diff.py`, `services/test/test_fingerprint.py`, `services/test/test_usages_diff.py`
- Modify: `services/test/test_etl_crash_safety.py` (keep `etl_glam` + sample-config tests only)

**Interfaces:**
- Consumes: nothing
- Produces: pytest no longer imports `fingerprint` / `usages_diff` / `_fn`. `cd services && pytest` still passes.

- [ ] **Step 1: Replace `test_etl_crash_safety.py` with the tests that actually run Node/Python**

The whole file becomes:

```python
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("etl.glams_table", MagicMock())

from etl.etl_glam import _etl, process_glam  # noqa: E402


@patch("etl.etl_glam.logging")
@patch("etl.etl_glam.subprocess.Popen")
def test_etl_logs_stderr_and_raises_on_nonzero(mock_popen, mock_log):
    proc = mock_popen.return_value
    proc.stdout = io.StringIO("Category missing on replica: Foo\n")
    proc.args = ["node", "etl.js", "NARA"]
    proc.wait.return_value = 65
    with pytest.raises(subprocess.CalledProcessError) as ei:
        _etl("NARA")
    assert ei.value.returncode == 65
    kw = mock_popen.call_args.kwargs
    assert kw["stdout"] == subprocess.PIPE
    assert kw["stderr"] == subprocess.STDOUT
    assert kw.get("capture_output") is not True
    mock_log.info.assert_any_call("%s", "Category missing on replica: Foo")
    mock_log.error.assert_any_call("etl.js returncode %s", 65)


@patch("etl.etl_glam.logging")
@patch("etl.etl_glam.subprocess.Popen")
def test_etl_logs_stdout_on_success(mock_popen, mock_log):
    proc = mock_popen.return_value
    proc.stdout = io.StringIO("done\n")
    proc.args = ["node", "etl.js", "NARA"]
    proc.wait.return_value = 0
    _etl("NARA")
    mock_log.info.assert_any_call("%s", "done")
    mock_log.error.assert_not_called()


@patch("etl.etl_glam.update_to_failed")
@patch("etl.etl_glam._etl")
def test_process_glam_marks_failed_on_exit_65(mock_etl, mock_failed):
    mock_etl.side_effect = subprocess.CalledProcessError(65, ["node"])
    glam = {"name": "NARA", "lastrun": None}
    process_glam(glam)
    mock_failed.assert_called_once_with(glam)


def test_sample_config_drops_category_hard_limit():
    cfg = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "config.sample.json").read_text()
    )
    assert "categories" not in cfg["limits"]
    assert "images" in cfg["limits"]
```

- [ ] **Step 2: Delete the unused modules and their grep tests**

```bash
git rm services/etl/fingerprint.py services/etl/usages_diff.py \
  services/test/test_fingerprint.py services/test/test_usages_diff.py
```

- [ ] **Step 3: Run pytest**

Run: `cd services && pytest -q`

Expected: PASS. No `ModuleNotFoundError: fingerprint` / `usages_diff`. No leftover `_fn` in `services/test/`.

- [ ] **Step 4: Commit**

```bash
git add services/test/test_etl_crash_safety.py
git commit -m "$(cat <<'EOF'
test: drop unused Python ETL twins and source greps

EOF
)"
```

---

### Task 3: One usage path and transactional parent replace

**Files:**
- Modify: `services/etl/etl.js`
- Modify: `services/etl/SQL/functions.sql`
- Modify: `services/test/test_etl_crash_safety.py`
- Delete: `services/etl/SQL/add_category.sql`

**Interfaces:**
- Consumes: `groupCatParents` from `./etl_logic.js`
- Produces: crawler always set-diffs; leftover `gil_page IS NULL` closed after `addUsage`; `finalize()` (no args) upserts full parent arrays on one pool client then deletes orphans then marks images then `doMaintenance`. Upsert columns are `page_title, cat_subcats, cat_files, cl_to, cat_level` only. No `AddCategory` in the database after migrate (Task 4 applies this file).

- [ ] **Step 1: Write the failing contract test**

Append to `services/test/test_etl_crash_safety.py` (after Task 2 this file has no `_fn`):

```python
ETL_JS = Path(__file__).resolve().parents[1] / "etl" / "etl.js"


def test_etl_js_finalize_contract():
    src = ETL_JS.read_text()
    compact = src.replace(" ", "")
    assert "BEGIN" in src
    assert "ON CONFLICT" in src
    assert "cl_to='{}'" not in compact
    assert "usageSetDiffRan" not in src
    close = "UPDATE usages SET is_alive=false WHERE is_alive = true AND gil_page IS NULL"
    assert src.index(close) < src.index("await finalize()")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services && pytest test/test_etl_crash_safety.py::test_etl_js_finalize_contract -v`

Expected: FAIL (`BEGIN` / `ON CONFLICT` missing, or wipe / `usageSetDiffRan` still present)

- [ ] **Step 3: Wire `etl.js`**

At the top, after the `mariadb` require:

```javascript
const { groupCatParents } = require("./etl_logic.js");
```

Delete globals `usageSetDiffRan` and `categorySql`.

Replace `finalize` with:

```javascript
async function finalize() {
  log("Number of categories: " + catFreeTail);
  log("Number of images: " + countImages);
  const rows = groupCatParents(catQueue, catFreeTail);
  const titles = rows.map((r) => r.page_title);
  const client = await glam.connection.connect();
  try {
    await client.query("BEGIN");
    for (const r of rows) {
      await client.query(
        "INSERT INTO categories (page_title, cat_subcats, cat_files, cl_to, cat_level) " +
          "VALUES ($1, $2, $3, $4, $5) " +
          "ON CONFLICT (page_title) DO UPDATE SET " +
          "cat_subcats = EXCLUDED.cat_subcats, cat_files = EXCLUDED.cat_files, " +
          "cl_to = EXCLUDED.cl_to, cat_level = EXCLUDED.cat_level",
        [r.page_title, r.cat_subcats, r.cat_files, r.cl_to, r.cat_level]
      );
    }
    if (titles.length) {
      await client.query(
        "DELETE FROM categories WHERE page_title <> ALL($1::varchar[])",
        [titles]
      );
    }
    await client.query(
      "UPDATE images SET is_alive = false WHERE NOT (cl_to && (SELECT array_agg(page_title) FROM categories))"
    );
    await client.query("select * from doMaintenance();");
    await client.query("COMMIT");
  } catch (e) {
    try {
      await client.query("ROLLBACK");
    } catch (_) {}
    throw e;
  } finally {
    client.release();
  }
}
```

Replace `afterCategories` with:

```javascript
async function afterCategories() {
  log("===========================================");
  log("Now loading images...");
  catHead = 0;
  imgIndex = 0;
  await loadImages();
}
```

In `loadImages`, delete the comment `// keep in sync with etl/fingerprint.py`. Leave the four-line count compare and the existing `UPDATE categories SET last_crawled_at = NOW() WHERE page_title = $1` — that is the only stamp. Finalize must not mention `last_crawled_at`.

Replace `flushUsages` so the skip does not mention `usageSetDiffRan`:

```javascript
async function flushUsages() {
  let i = 0;
  let storage_query = "";
  while (i < usagindex) {
    let use = usages[i];
    if (use.gil_page != null && use.gil_page !== "") {
      const key =
        String(use.gil_to) +
        "\0" +
        String(use.gil_wiki) +
        "\0" +
        String(use.gil_page);
      if (usageOurs.has(key)) {
        usageOurs.delete(key);
        i++;
        continue;
      }
    }
    let sql =
      "select * from addUsage('" +
      use.gil_wiki +
      "','" +
      use.gil_page_title.replace(/'/g, "''") +
      "','" +
      use.gil_to.replace(/'/g, "''") +
      "'";
    if (use.gil_page != null && use.gil_page !== "") {
      sql += "," + Number(use.gil_page);
    }
    storage_query += sql + ");\r\n";
    i++;
  }
  if (storage_query) {
    log("Updating Postgres data...");
    await glam.connection.query(storage_query);
  }
  usages = [];
  usagindex = 0;
}
```

Replace `afterUsages` and `prepareUsages`:

```javascript
async function afterUsages() {
  await flushUsages();
  await closeVanishedUsages();
  await glam.connection.query(
    "UPDATE usages SET is_alive=false WHERE is_alive = true AND gil_page IS NULL"
  );
  await finalize();
}

async function prepareUsages() {
  usageOurs = new Set();
  const live = await glam.connection.query(
    "SELECT gil_to, gil_wiki, gil_page FROM usages WHERE is_alive = true AND gil_page IS NOT NULL"
  );
  for (const r of live.rows) {
    usageOurs.add(
      String(r.gil_to) + "\0" + String(r.gil_wiki) + "\0" + String(r.gil_page)
    );
  }
}
```

Leave `closeVanishedUsages` as it is today (keyed close + `last_seen` bump). Do not add `require.main`. Do not wrap the CLI entry.

- [ ] **Step 4: Run the contract test**

Run: `cd services && pytest test/test_etl_crash_safety.py::test_etl_js_finalize_contract -v`

Expected: PASS

- [ ] **Step 5: Drop `AddCategory` from SQL**

`services/etl/SQL/functions.sql` becomes (keep `AddImage` / `AddUsage` text exactly as today, only remove `AddCategory` and add the drop):

```sql
DROP FUNCTION IF EXISTS AddCategory(varchar, int, int, varchar, int);

create or replace function AddImage(_name varchar(255),_user varchar(255) ,_timestamp timestamp without time zone,size bigint,_to varchar(255))
returns void as $$
declare
t varchar(255)[];
opened boolean;
begin
SELECT is_alive into opened FROM images WHERE img_name = _name;
t[0]:=_to;
IF FOUND THEN
if opened then
update images set cl_to=array_remove(cl_to,_to)||_to where img_name=_name;
else
update images set cl_to=t, is_alive=true where img_name=_name;
end if;
else
insert into images(img_name,img_user_text,img_timestamp,img_size,cl_to,is_alive) values(_name,_user,_timestamp,size,t,true);
end if;
EXCEPTION
    WHEN others THEN
        RAISE NOTICE 'Error on %',_name;
end;
$$language plpgsql;


DROP FUNCTION IF EXISTS AddUsage(varchar, varchar, varchar);
create or replace function AddUsage(_wiki varchar(20),_page varchar(255) ,_to varchar(255), _gil_page int default null)
returns void as $$
declare
appeared date;
begin
SELECT first_seen into appeared FROM usages WHERE gil_page_title = _page and gil_to=_to and gil_wiki=_wiki and is_alive=true;
IF FOUND THEN
update usages set last_seen=CURRENT_DATE, gil_page=COALESCE(_gil_page, gil_page) where gil_page_title=_page and gil_to=_to and gil_wiki=_wiki and first_seen=appeared;
else
insert into usages(gil_wiki,gil_page_title,gil_to,first_seen,last_seen,is_alive,gil_page) values(_wiki,_page,_to,CURRENT_DATE,CURRENT_DATE,true,_gil_page);
end if;
end;
$$language plpgsql;
```

```bash
git rm services/etl/SQL/add_category.sql
```

- [ ] **Step 6: Run tests**

Run: `cd services && node --test test/test_etl_logic.js && pytest -q`

Expected: PASS

Grep: `rg AddCategory services/etl` matches only the `DROP FUNCTION` line. `rg add_category` matches nothing under `services/etl`. `rg last_crawled_at services/etl/etl.js` matches only the `loadImages` UPDATE.

- [ ] **Step 7: Commit**

```bash
git add services/etl/etl.js services/etl/SQL/functions.sql \
  services/test/test_etl_crash_safety.py
git commit -m "$(cat <<'EOF'
fix: replace category parents in one transaction; always set-diff usages

EOF
)"
```

---

### Task 4: Whole-file SQL apply through `setup.js`

**Files:**
- Modify: `services/etl/setup.js`
- Modify: `services/etl/migrate_orgs.py`
- Modify: `services/test/test_migrate_orgs.py`
- Modify: `README.md` (Nightly ops migrate paragraph only)

**Interfaces:**
- Consumes: `drop_view_or_matview(cur, name)` (keep as in worktree). `setup.js` argv: `node setup.js <GlamName>` → default file list; `node setup.js <GlamName> incremental.sql functions.sql maintenance.sql` → those three only.
- Produces: migrate drops the two views then subprocesses that second form. `new_glam_listener.py` still calls `node … etl/setup.js <name>` with no extra args. SQL error in `setup.js` exits 1. No `sql_statements`, no `apply_sql`, no `ADD_CATEGORY_SQL`.

- [ ] **Step 1: Rewrite `test_migrate_orgs.py`**

The whole file becomes:

```python
import re
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("etl.glams_table", MagicMock())

from etl.migrate_orgs import drop_view_or_matview, main  # noqa: E402


class _KindCur:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(sql)

    def fetchone(self):
        return None if self.kind is None else (self.kind,)


def test_drop_view_or_matview_view():
    cur = _KindCur("v")
    drop_view_or_matview(cur, "visualizations_stats")
    assert any(re.search(r"drop\s+view\s+visualizations_stats\b", s, re.I) for s in cur.calls)
    assert not any(re.search(r"drop\s+materialized\s+view", s, re.I) for s in cur.calls)


def test_drop_view_or_matview_matview():
    cur = _KindCur("m")
    drop_view_or_matview(cur, "visualizations_stats")
    assert any(
        re.search(r"drop\s+materialized\s+view\s+visualizations_stats\b", s, re.I)
        for s in cur.calls
    )


def test_drop_view_or_matview_missing():
    cur = _KindCur(None)
    drop_view_or_matview(cur, "visualizations_stats")
    assert not any(re.search(r"^drop\s", s.strip(), re.I) for s in cur.calls)


def test_main_exits_nonzero_after_any_glam_error():
    nara = MagicMock()
    met = MagicMock()
    conns = {"nara": nara, "met": met}

    def fake_run(argv, check=False, **kwargs):
        assert argv[0] == "node"
        assert argv[1].endswith("setup.js")
        assert argv[3:] == ["incremental.sql", "functions.sql", "maintenance.sql"]
        if argv[2] == "NARA":
            raise subprocess.CalledProcessError(1, argv)
        return MagicMock(returncode=0)

    with patch(
        "etl.migrate_orgs.get_active_glams",
        return_value=[
            {"name": "NARA", "database": "nara"},
            {"name": "MET", "database": "met"},
        ],
    ), patch(
        "etl.migrate_orgs.get_glam_database_connection",
        side_effect=lambda db: conns[db],
    ), patch(
        "etl.migrate_orgs.subprocess.run",
        side_effect=fake_run,
    ):
        with pytest.raises(SystemExit) as ei:
            main()
    assert ei.value.code == 1
    nara.close.assert_called_once()
    met.close.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services && pytest test/test_migrate_orgs.py::test_main_exits_nonzero_after_any_glam_error -v`

Expected: FAIL (`etl.migrate_orgs.subprocess.run` not used, or import error if `sql_statements` still required by the old test file you already replaced).

- [ ] **Step 3: Replace `migrate_orgs.py`**

```python
import pathlib
import subprocess
import sys
from etl.glams_table import get_active_glams, get_glam_database_connection

SETUP_JS = pathlib.Path(__file__).resolve().with_name("setup.js")
APPLY_FILES = ("incremental.sql", "functions.sql", "maintenance.sql")


def drop_view_or_matview(cur, name):
    cur.execute(
        "SELECT c.relkind FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = %s",
        (name,),
    )
    row = cur.fetchone()
    if not row:
        return
    kind = row[0]
    if kind == "m":
        cur.execute(f"DROP MATERIALIZED VIEW {name}")
    elif kind == "v":
        cur.execute(f"DROP VIEW {name}")


def apply_sql_files(glam_name):
    subprocess.run(
        ["node", str(SETUP_JS), glam_name, *APPLY_FILES],
        check=True,
    )


def main():
    failed = False
    for glam in get_active_glams():
        conn = get_glam_database_connection(glam["database"])
        try:
            cur = conn.cursor()
            drop_view_or_matview(cur, "visualizations_stats")
            drop_view_or_matview(cur, "visualizations_sum")
            apply_sql_files(glam["name"])
            print("migrated", glam["name"])
        except Exception as e:
            failed = True
            print("failed", glam["name"], e, file=sys.stderr)
        finally:
            conn.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Replace `setup.js`**

Do not `module.exports`. Trailing argv filenames override the default list. Query error exits 1.

```javascript
var config = require("../config/config.js");
var fs = require("fs");
var path = require("path");

var DEFAULT_FILES = [
    "db_init.sql",
    "dailyInsert.sql",
    "functions.sql",
    "maintenance.sql",
    "incremental.sql",
];

async function runSqlFiles(db, files) {
    for (var i = 0; i < files.length; i++) {
        var filetoRead = path.join(__dirname, "SQL", files[i]);
        console.log(filetoRead);
        await db.query(fs.readFileSync(filetoRead, "utf8"));
    }
}

async function main() {
    if (process.argv.length < 3) {
        console.log("Missing GLAM name");
        process.exit(1);
    }
    var name = process.argv[2];
    var files = process.argv.length > 3 ? process.argv.slice(3) : DEFAULT_FILES;
    var glams = await config.loadGlams();
    var glam = glams[name];
    if (glam === undefined) {
        console.log("Unknown GLAM name");
        process.exit(1);
    }
    console.log("Setup started");
    try {
        await runSqlFiles(glam.connection, files);
    } catch (err) {
        console.log("Error applying SQL");
        console.log(err);
        process.exit(1);
    }
    console.log("Setup ended");
    process.exit(0);
}

main();
```

- [ ] **Step 5: README Nightly ops migrate paragraph**

Replace the two sentences above the migrate command (the “lacks 4-arg `AddUsage`” line) with:

```markdown
Migrate org DBs (run once after deploying schema changes). Applies `incremental.sql`, `functions.sql`, and `maintenance.sql` as whole files. The process must exit 0 — a `failed` line means that org did not finish; re-run migrate. Do not start cron until every active DB prints `migrated`.
```

Leave the rest of README (first-night recrawl, carnavalize, etc.).

- [ ] **Step 6: Run tests**

Run: `cd services && pytest -q && node --test test/test_etl_logic.js`

Expected: PASS

Confirm: `rg sql_statements services/etl` no matches. `rg --apply services/etl/setup.js` no matches. `new_glam_listener.py` still `etl/setup.js', name`.

- [ ] **Step 7: Commit**

```bash
git add services/etl/setup.js services/etl/migrate_orgs.py \
  services/test/test_migrate_orgs.py README.md
git commit -m "$(cat <<'EOF'
fix: apply org SQL as whole files through setup.js

EOF
)"
```

---

## Self-review (spec coverage)

| Spec item | Task |
|---|---|
| Delete fingerprint.py / usages_diff.py | 2 |
| Delete add_category.sql; drop AddCategory | 3 |
| Whole-file SQL via node-pg / setup.js | 4 (trailing files, not `--apply`) |
| Delete sql_statements | 4 |
| groupCatParents in etl_logic.js | 1 |
| afterCategories no addCategory | 3 |
| finalize replace arrays, one client txn | 3 (INSERT loop, not jsonb; no `last_crawled_at`) |
| Always set-diff; NULL leftover close; no 5-day | 3 |
| Contract: BEGIN / ON CONFLICT / no blank / no flag / NULL close before finalize | 3 |
| Inline fingerprint compare; only `loadImages` stamps `last_crawled_at` | 3 |
| Drop `_fn()` greps; keep etl_glam tests | 2 |
| README migrate blurb (`failed` = did not finish, re-run) | 4 |
| wikiOpen exit 65 | untouched |
| first_seen preserved (no migrate usage writes) | 4 does not touch usages |
| usageKey module / require.main / setup exports | dropped on purpose |
