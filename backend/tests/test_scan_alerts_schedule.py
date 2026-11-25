"""The daily smart-alert scan has time to reach every shop (launch run 13 D2).

In production the scan ran as one job over 52 shops and hit the worker's
120 s default timeout on every run, so no smart alert was ever written.
Read from the source: importing the worker needs a Redis URL.
"""

from __future__ import annotations

import ast
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "app" / "worker" / "main.py"


def _constants(tree: ast.Module) -> dict[str, object]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def test_the_alert_scan_cron_has_its_own_long_timeout() -> None:
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    timeout = _constants(tree)["SCAN_ALERTS_TIMEOUT_S"]
    assert isinstance(timeout, int) and timeout >= 900
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "cron"
        and node.args
        and getattr(node.args[0], "id", None) == "scan_alerts"
    ]
    assert len(calls) == 1
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    assert getattr(keywords.get("timeout"), "id", None) == "SCAN_ALERTS_TIMEOUT_S"
