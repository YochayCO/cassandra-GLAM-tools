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
