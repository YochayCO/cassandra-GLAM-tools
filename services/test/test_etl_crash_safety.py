import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ETL_JS = Path(__file__).resolve().parents[1] / "etl" / "etl.js"

sys.modules.setdefault("etl.glams_table", MagicMock())

from etl.etl_glam import _etl, process_glam  # noqa: E402


def _fn(name: str) -> str:
    src = ETL_JS.read_text()
    m = re.search(rf"async function {name}\b", src)
    assert m, name
    nxt = re.search(r"\nasync function |\nfunction |\nvar [A-Za-z]", src[m.end() :])
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start() : end]


def test_wiki_open_exits_65_when_replica_category_missing():
    body = _fn("wikiOpen")
    assert "Category missing on replica" in body
    assert "process.exit(65)" in body
    assert "rows.length === 0" in body


def test_finalize_true_exits_65_without_wipes():
    body = _fn("finalize")
    assert "process.exit(65)" in body
    assert "delete from categories" not in body.lower()
    assert "is_alive=false" not in body.replace(" ", "").lower()
    assert "doMaintenance" in body


def test_after_categories_does_not_wipe_or_write():
    body = _fn("afterCategories")
    assert "delete from categories" not in body.lower()
    assert "is_alive=false" not in body.replace(" ", "").lower()
    assert "glam.connection.query" not in body
    assert "addCategory" in body


def test_finalize_runs_buffered_categories_with_maintenance():
    src = ETL_JS.read_text()
    finalize = _fn("finalize")
    after = _fn("afterCategories")
    assert "doMaintenance" in finalize
    assert "glam.connection.query" in finalize
    assert "addCategory" in after
    # buffer is written only on the success path
    assert "failure === true" in finalize
    assert src.index("process.exit(65)") < src.index('select * from doMaintenance()')


@patch("etl.etl_glam.logging")
@patch("etl.etl_glam.subprocess.run")
def test_etl_logs_stderr_and_raises_on_nonzero(mock_run, mock_log):
    mock_run.return_value = subprocess.CompletedProcess(
        args=["node", "etl.js", "NARA"],
        returncode=65,
        stdout="",
        stderr="category missing",
    )
    with pytest.raises(subprocess.CalledProcessError) as ei:
        _etl("NARA")
    assert ei.value.returncode == 65
    assert ei.value.stderr == "category missing"
    assert mock_run.call_args.kwargs["capture_output"] is True
    assert mock_run.call_args.kwargs["text"] is True
    mock_log.error.assert_any_call("etl.js stderr: %s", "category missing")
    mock_log.error.assert_any_call("etl.js returncode %s", 65)


@patch("etl.etl_glam.logging")
@patch("etl.etl_glam.subprocess.run")
def test_etl_logs_stdout_on_success(mock_run, mock_log):
    mock_run.return_value = subprocess.CompletedProcess(
        args=["node", "etl.js", "NARA"],
        returncode=0,
        stdout="done",
        stderr="",
    )
    _etl("NARA")
    mock_log.info.assert_any_call("done")
    mock_log.error.assert_not_called()


@patch("etl.etl_glam.update_to_failed")
@patch("etl.etl_glam._etl")
def test_process_glam_marks_failed_on_exit_65(mock_etl, mock_failed):
    mock_etl.side_effect = subprocess.CalledProcessError(65, ["node"])
    glam = {"name": "NARA", "lastrun": None}
    process_glam(glam)
    mock_failed.assert_called_once_with(glam)
