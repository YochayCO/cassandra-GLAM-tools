import re
from pathlib import Path

from etl.usages_diff import diff_usage

ROOT = Path(__file__).resolve().parents[1]
ETL_JS = ROOT / "etl" / "etl.js"
FUNCTIONS_SQL = ROOT / "etl" / "SQL" / "functions.sql"
MAINTENANCE_SQL = ROOT / "etl" / "SQL" / "maintenance.sql"
MIGRATE_ORGS = ROOT / "etl" / "migrate_orgs.py"


def _fn(name: str) -> str:
    src = ETL_JS.read_text()
    m = re.search(rf"async function {name}\b", src)
    assert m, name
    nxt = re.search(
        r"\n(?:async )?function [A-Za-z]|\nvar [A-Za-z].*= function",
        src[m.end() :],
    )
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start() : end]


def _sql_fn(path: Path, name: str) -> str:
    src = path.read_text()
    m = re.search(rf"create or replace function {name}\b", src, re.I)
    assert m, name
    nxt = re.search(r"create or replace function ", src[m.end() :], re.I)
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start() : end]


def test_insert_and_close():
    ours = {("A.jpg", "enwiki", 1), ("A.jpg", "enwiki", 2)}
    replica = {("A.jpg", "enwiki", 2), ("A.jpg", "enwiki", 3)}
    ins, close = diff_usage(ours, replica)
    assert ins == {("A.jpg", "enwiki", 3)}
    assert close == {("A.jpg", "enwiki", 1)}


def test_build_usage_query_selects_gil_page():
    src = ETL_JS.read_text()
    start = src.index("var buildUsageQuery")
    end = src.index("var buildImageQuery")
    body = src[start:end]
    assert "SELECT gil_wiki, gil_page, gil_page_title, gil_to" in body
    assert "updated_at" not in body


def test_load_usages_keeps_5000_flush_and_tail_return():
    src = ETL_JS.read_text()
    body = _fn("loadUsages")
    assert "var USAGE_FLUSH = 5000" in src
    assert "usagindex >= USAGE_FLUSH" in body
    assert "flushUsages" in body
    assert "await loadUsages()" not in body
    assert "return loadUsages()" in body


def test_load_usages_stores_gil_page():
    body = _fn("loadUsages")
    assert "usages[usagindex].gil_page =" in body
    assert "usages[usagindex].gil_page_title" in body


def test_flush_usages_addusage_includes_gil_page():
    body = _fn("flushUsages")
    assert "addUsage" in body
    assert re.search(r"use\.gil_page(?![_\w])", body)


def test_set_diff_closes_vanished_and_skips_stale_lag():
    src = ETL_JS.read_text()
    finalize = _fn("finalize")
    compact = src.replace(" ", "")
    assert "doMaintenance" in finalize
    assert "usageSetDiffRan" in src
    assert "UPDATE usages SET is_alive=false" in src or "update usages set is_alive=false" in src.lower()
    assert "gil_page IS NOT NULL" in src
    assert "gil_page IS NULL" in src
    maint = _sql_fn(MAINTENANCE_SQL, "doMaintenance")
    assert "dailyImageUsage" in maint
    assert "last_seen<(current_date-5)" not in maint.replace(" ", "")
    assert "last_seen<(current_date-5)" in compact
    assert "usageSetDiffRan" in finalize
    assert "functions.sql" in MIGRATE_ORGS.read_text()
    assert "maintenance.sql" in MIGRATE_ORGS.read_text()


def test_add_usage_stores_gil_page():
    src = _sql_fn(FUNCTIONS_SQL, "AddUsage")
    compact = re.sub(r"\s+", " ", src).lower()
    assert "_gil_page" in compact
    assert "gil_page" in compact.split("insert into usages")[1]
