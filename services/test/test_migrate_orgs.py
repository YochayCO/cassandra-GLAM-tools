import re
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("etl.glams_table", MagicMock())

from etl.migrate_orgs import (  # noqa: E402
    ADD_CATEGORY_SQL,
    FUNCTIONS_SQL,
    MAINTENANCE_SQL,
    SQL,
    apply_sql,
    main,
    sql_statements,
)


def _creates(stmts):
    return [s for s in stmts if re.search(r"create(?:\s+or\s+replace)?\s+function\b", s, re.I)]


def test_functions_sql_one_create_or_drop_per_statement():
    stmts = sql_statements(FUNCTIONS_SQL)
    creates = _creates(stmts)
    assert len(creates) == 3
    assert all(
        len(re.findall(r"create(?:\s+or\s+replace)?\s+function\b", s, re.I)) == 1
        for s in creates
    )
    drops = [s for s in stmts if re.search(r"drop\s+function\b", s, re.I)]
    assert len(drops) == 1
    assert not re.search(r"\bcreate\b", drops[0], re.I)
    addusage = [s for s in creates if re.search(r"function\s+addusage\b", s, re.I)][0]
    assert re.search(r"_gil_page\s+int", addusage, re.I)
    assert re.search(r"\$\$\s*language\s+plpgsql\s*;", addusage, re.I)


def test_maintenance_sql_splits_table_from_function():
    stmts = sql_statements(MAINTENANCE_SQL)
    assert any(re.search(r"create\s+table\b", s, re.I) for s in stmts)
    creates = _creates(stmts)
    assert len(creates) == 1
    assert re.search(r"function\s+domaintenance\b", creates[0], re.I)
    table = [s for s in stmts if re.search(r"create\s+table\b", s, re.I)][0]
    assert "function" not in table.lower()


def test_add_category_sql_two_functions():
    stmts = sql_statements(ADD_CATEGORY_SQL)
    assert len(_creates(stmts)) == 2


class _Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(sql)


def test_apply_sql_records_one_execute_per_statement():
    cur = _Cur()
    apply_sql(cur, FUNCTIONS_SQL)
    apply_sql(cur, MAINTENANCE_SQL)
    assert any(re.search(r"function\s+addusage\b", s, re.I) and "_gil_page" in s for s in cur.calls)
    assert all(
        len(re.findall(r"create(?:\s+or\s+replace)?\s+function\b", s, re.I)) <= 1
        for s in cur.calls
    )
    assert any(re.search(r"create\s+table\b", s, re.I) for s in cur.calls)
    assert any(re.search(r"function\s+domaintenance\b", s, re.I) for s in cur.calls)


def test_incremental_sql_still_splits_on_semicolons():
    stmts = sql_statements(SQL)
    assert any(re.search(r"create\s+or\s+replace\s+view\s+visualizations_sum\b", s, re.I) for s in stmts)
    assert all(";" not in s for s in stmts)


def test_main_exits_nonzero_after_any_glam_error():
    failed = MagicMock()
    failed.cursor.return_value.execute.side_effect = Exception("can't execute multiple commands at once")
    ok = MagicMock()
    conns = {"nara": failed, "met": ok}

    with patch(
        "etl.migrate_orgs.get_active_glams",
        return_value=[
            {"name": "NARA", "database": "nara"},
            {"name": "MET", "database": "met"},
        ],
    ), patch(
        "etl.migrate_orgs.get_glam_database_connection",
        side_effect=lambda db: conns[db],
    ):
        with pytest.raises(SystemExit) as ei:
            main()
    assert ei.value.code == 1
    failed.close.assert_called_once()
    ok.close.assert_called_once()
    ok.cursor.return_value.execute.assert_called()
