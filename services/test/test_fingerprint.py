import re
from pathlib import Path

from etl.fingerprint import fingerprint_matches

ROOT = Path(__file__).resolve().parents[1]
ETL_JS = ROOT / "etl" / "etl.js"
FUNCTIONS_SQL = ROOT / "etl" / "SQL" / "functions.sql"
ADD_CATEGORY_SQL = ROOT / "etl" / "SQL" / "add_category.sql"


def test_match():
    assert fingerprint_matches(10, 2, 10, 2) is True


def test_file_count_change():
    assert fingerprint_matches(10, 2, 11, 2) is False


def test_missing_stored():
    assert fingerprint_matches(None, None, 1, 0) is False


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


def _compact(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def test_load_images_skips_when_counts_match():
    body = _fn("loadImages")
    assert "// keep in sync with etl/fingerprint.py" in body
    assert (
        "SELECT cat_files, cat_subcats FROM categories WHERE page_title = $1"
        in body
    )
    assert "Number(prev.rows[0].cat_files) === Number(catQueue[catHead].cat_files)" in body
    assert (
        "Number(prev.rows[0].cat_subcats) === Number(catQueue[catHead].cat_subcats)"
        in body
    )
    assert "prev.rows[0].cat_files != null" in body
    assert body.index("keep in sync") < body.index("buildImageQuery")


def test_load_images_stamps_last_crawled_at():
    body = _fn("loadImages")
    assert "UPDATE categories SET last_crawled_at = NOW()" in body
    assert body.index("buildImageQuery") < body.index("last_crawled_at")


def test_load_images_pages_after_fingerprint():
    src = ETL_JS.read_text()
    body = _fn("loadImages")
    page = '" LIMIT " + IMAGE_PAGE + " OFFSET "'
    assert "var IMAGE_PAGE = 10000" in src
    assert page in body
    assert body.index("keep in sync") < body.index("buildImageQuery")
    assert body.index("buildImageQuery") < body.index(page)


def test_load_images_collects_names_across_pages_before_strip():
    body = _fn("loadImages")
    assert "for (;;)" in body
    assert "rows.length < IMAGE_PAGE" in body
    assert "offset += IMAGE_PAGE" in body
    assert body.index("const names") < body.index("for (;;)")
    assert body.index("names.push") < body.index("rows.length < IMAGE_PAGE")
    assert body.index("offset += IMAGE_PAGE") < body.index("array_remove")
    assert body.index("rows.length < IMAGE_PAGE") < body.index("array_remove")
    assert body.index("loadImagesIntoDB") < body.index("array_remove")


def test_build_image_query_orders_by_img_name():
    src = ETL_JS.read_text()
    start = src.index("var buildImageQuery")
    end = src.index("var buildCategoryQuery")
    body = src[start:end]
    assert "ORDER BY img_name" in body
    assert "LIMIT" not in body
    assert "OFFSET" not in body


def test_finalize_clears_parent_arrays_before_addcategory():
    body = _fn("finalize")
    clear = "UPDATE categories SET cl_to='{}', cat_level='{}' WHERE page_title = ANY($1)"
    assert clear in body
    assert body.index("process.exit(65)") < body.index(clear)
    assert body.index(clear) < body.index("categorySql")
    assert "images SET cl_to" not in body


def test_finalize_deletes_vanished_categories_after_success():
    body = _fn("finalize")
    assert "DELETE FROM categories WHERE page_title <> ALL($1::varchar[])" in body
    assert body.index("process.exit(65)") < body.index(
        "DELETE FROM categories WHERE page_title <> ALL($1::varchar[])"
    )


def test_finalize_marks_images_without_remaining_categories():
    body = _fn("finalize")
    stmt = (
        "UPDATE images SET is_alive = false WHERE NOT "
        "(cl_to && (SELECT array_agg(page_title) FROM categories))"
    )
    assert stmt in body
    assert body.index("process.exit(65)") < body.index(stmt)
    assert body.index("DELETE FROM categories") < body.index(stmt)


def test_add_category_assigns_counts_and_appends_parents():
    for path in (ADD_CATEGORY_SQL, FUNCTIONS_SQL):
        existing = _compact(_sql_fn(path, "AddCategory")).split("else")[0]
        compact = existing.replace(" ", "")
        assert "cat_files=files" in compact
        assert "cat_subcats=subcats" in compact
        assert "cl_to=cl_to||_to" in compact
        assert "cat_level=cat_level||_level" in compact
        assert "cl_to=t" not in compact


def test_add_image_replaces_membership_on_alive_row():
    for path in (ADD_CATEGORY_SQL, FUNCTIONS_SQL):
        src = _compact(_sql_fn(path, "AddImage"))
        opened = src.split("if opened then")[1].split("else")[0]
        assert "cl_to=cl_to||_to" not in opened.replace(" ", "")
