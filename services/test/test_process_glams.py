import sys
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

sys.modules.setdefault("lib.sentry", MagicMock())
sys.modules.setdefault("etl.glams_table", MagicMock())
sys.modules.setdefault("etl.download_mediacounts", MagicMock())

from daily import process_glams
from etl.etl_glam import process_glam


class _Pool:
    def __init__(self, max_workers):
        self.max_workers = max_workers
        self.submitted = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, glam):
        self.submitted.append((fn, glam))
        fut = Future()
        if glam["name"] == "bad":
            fut.set_exception(RuntimeError("boom"))
        else:
            fut.set_result(None)
        return fut


def test_process_glams_uses_pool_drops_conn_continues_on_failure():
    pools = []

    def factory(*, max_workers):
        p = _Pool(max_workers)
        pools.append(p)
        return p

    glams = [
        {"name": "bad", "lastrun": None, "conn": object()},
        {"name": "good", "lastrun": None, "conn": object()},
    ]
    with patch("daily.ProcessPoolExecutor", factory), patch(
        "daily.os.cpu_count", return_value=32
    ), patch("daily.logging") as log:
        process_glams(glams)

    assert pools[0].max_workers == 8
    submitted = pools[0].submitted
    assert [g["name"] for _, g in submitted] == ["bad", "good"]
    assert all(fn is process_glam for fn, _ in submitted)
    assert all("conn" not in g for _, g in submitted)
    log.exception.assert_called_once()
    assert log.exception.call_args.args == ("snapshot failed %s", "bad")
