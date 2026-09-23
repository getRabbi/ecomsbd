"""Courier manifests are part of the deployed application.

The production image is built from the ``backend`` directory and copies only
``app``, ``migrations`` and ``alembic.ini``. The manifests used to live in the
repository's ``docs/``, which that image does not contain, so production loaded
none: the provider list was empty, every connect was refused with "has no
integration manifest", and no courier could be booked — while every test here
passed, because the test run could see the whole repository.

These tests would have failed then. The second one reproduces the image's
layout rather than trusting a path computed in the source tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import app
from app.couriers.capabilities import MANIFEST_DIR, load_all_manifests

#: Every courier the product knows about, including the ones it cannot yet use.
EXPECTED_PROVIDERS = {"manual", "pathao", "redx", "steadfast"}


def test_manifests_ship_inside_the_app_package() -> None:
    package_dir = Path(app.__file__).resolve().parent

    assert MANIFEST_DIR.is_relative_to(package_dir)
    assert set(load_all_manifests()) == EXPECTED_PROVIDERS


def test_manifests_load_from_the_app_directory_alone(tmp_path: Path) -> None:
    """Copy ``app`` the way the Dockerfile does and load the manifests there."""
    source = Path(app.__file__).resolve().parent
    shutil.copytree(source, tmp_path / "app", ignore=shutil.ignore_patterns("__pycache__"))

    script = (
        "import json\n"
        "from app.couriers import capabilities\n"
        "print(json.dumps({\n"
        "    'module': capabilities.__file__,\n"
        "    'providers': sorted(capabilities.load_all_manifests()),\n"
        "}))\n"
    )
    # A fresh interpreter, so nothing already imported from the source tree
    # can stand in for the copy. The command is fixed; nothing here is input.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    loaded = json.loads(result.stdout.strip().splitlines()[-1])

    # The copy, not the source tree, is what was imported.
    assert Path(loaded["module"]).resolve().is_relative_to(tmp_path.resolve())
    assert set(loaded["providers"]) == EXPECTED_PROVIDERS
