import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("etl.glams_table", MagicMock())

from etl.etl_glam import _etl, process_glam  # noqa: E402

ETL_JS = Path(__file__).resolve().parents[1] / "etl" / "etl.js"


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


def test_etl_js_finalize_contract():
    src = ETL_JS.read_text()
    compact = src.replace(" ", "")
    assert "BEGIN" in src
    assert "ON CONFLICT" in src
    assert "cl_to='{}'" not in compact
    assert "usageSetDiffRan" not in src
    close = "UPDATE usages SET is_alive=false WHERE is_alive = true AND gil_page IS NULL"
    assert src.index(close) < src.index("await finalize()")
