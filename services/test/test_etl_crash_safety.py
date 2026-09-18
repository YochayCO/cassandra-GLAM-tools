import io
import json
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
    fail_line = [ln for ln in body.splitlines() if "Category missing" in ln][0]
    assert "console.error" in fail_line


def test_finalize_true_exits_65_without_wipes():
    body = _fn("finalize")
    assert "process.exit(65)" in body
    fail_prefix = body[: body.index("process.exit(65)")]
    assert "delete from categories" not in fail_prefix.lower()
    assert "is_alive=false" not in fail_prefix.replace(" ", "").lower()
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


def test_get_level_childs_does_not_exit_on_category_limit():
    body = _fn("getLevelChilds")
    assert 'config.limits["categories"]' not in body
    assert "finalize(true)" not in body
    assert "catFreeTail" in body


def test_load_images_image_limit_is_warning_not_exit():
    body = _fn("loadImages")
    assert 'config.limits["images"]' in body
    assert "exceeded limit in config" in body
    assert "finalize(true)" not in body
    assert "process.exit" not in body


def test_load_usages_flushes_every_5000():
    src = ETL_JS.read_text()
    body = _fn("loadUsages")
    assert "var USAGE_FLUSH = 5000" in src
    assert "usagindex >= USAGE_FLUSH" in body
    assert "flushUsages" in body


def test_load_usages_batch_tail_returns_not_awaits():
    body = _fn("loadUsages")
    assert "await loadUsages()" not in body
    assert "return loadUsages()" in body
    assert "updated_at" not in body


def test_after_usages_flushes_remainder_then_finalize():
    body = _fn("afterUsages")
    assert body.index("flushUsages") < body.index("finalize")


def test_flush_usages_resets_buffer_without_exiting():
    body = _fn("flushUsages")
    assert "addUsage" in body
    assert "usages = []" in body
    assert "usagindex = 0" in body
    assert "finalize" not in body
    assert "process.exit" not in body


def test_sample_config_drops_category_hard_limit():
    cfg = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "config.sample.json").read_text()
    )
    assert "categories" not in cfg["limits"]
    assert "images" in cfg["limits"]
