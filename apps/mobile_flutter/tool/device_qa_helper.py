"""Host helper for integration_test/app_qa_test.dart (device QA, launch run 13).

Run it on the computer next to `flutter drive`. It watches the phone's logcat for
the test's `QA_EVENT` lines and answers them:

* `share`: sends the Android share intent with a LAUNCH TEST chat message to
  the app's share target (the real "share a chat to ecomsbd" path);
* `print …`: when the system print preview opens, screenshots it and presses Back;
* `browser …`: when a browser opens, records the URL it opened, screenshots it
  and presses Back.

The test customer's phone comes from `OPERATOR_TEST_PHONE` in the git-ignored
`backend/.env` and is never printed. Output (screenshots, `watcher.json`) goes
to the git-ignored `qa-output/mobile/`. Create `qa-output/mobile/watcher.stop`
to stop it.

    python apps/mobile_flutter/tool/device_qa_helper.py [package]   # default com.ecomsbd.app
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "qa-output" / "mobile"
STOP = OUT / "watcher.stop"
PKG = sys.argv[1] if len(sys.argv) > 1 else "com.ecomsbd.app"
SHARE_ACTIVITY = "com.ecomsbd.app.intake.QuickOrderShareActivity"
BROWSERS = ("chrome", "browser", "mozilla", "sbrowser", "opera", "brave", "edge")


def _adb() -> str:
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        candidate = Path(sdk) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        if candidate.exists():
            return str(candidate)
    found = shutil.which("adb")
    if not found:
        raise SystemExit("adb not found: set ANDROID_HOME or put adb on PATH")
    return found


ADB = _adb()


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip().strip("'\"")
    return values


PHONE = _env()["OPERATOR_TEST_PHONE"]
SHARE_TEXT = f"Name: LAUNCH TEST, {PHONE}, Mirpur 10 Dhaka, 1ta Launch Test Panjabi 1200 taka"
log: list[dict[str, str]] = []


def sh(*args: str) -> str:
    return subprocess.run(
        [ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def note(kind: str, **fields: object) -> None:
    masked = {k: str(v).replace(PHONE, "017•••••" + PHONE[-3:]) for k, v in fields.items()}
    entry = {"t": time.strftime("%H:%M:%S"), "kind": kind, **masked}
    log.append(entry)
    print(json.dumps(entry, ensure_ascii=False), flush=True)
    (OUT / "watcher.json").write_text(
        json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def focus() -> str:
    match = re.search(
        r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^}]+)\}", sh("shell", "dumpsys", "window")
    )
    return match.group(1) if match else ""


def shot(name: str) -> str:
    data = subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True).stdout
    (OUT / f"{name}.png").write_bytes(data)
    return f"qa-output/mobile/{name}.png"


def back_until(done: callable, tries: int) -> None:
    for _ in range(tries):
        sh("shell", "input", "keyevent", "4")
        time.sleep(1.5)
        if done():
            return


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STOP.unlink(missing_ok=True)
    sh("logcat", "-c")
    seen, expect, count = 0, None, 0
    deadline = time.time() + 40 * 60
    note("start", pkg=PKG)
    while time.time() < deadline and not STOP.exists():
        lines = [
            line
            for line in sh("logcat", "-d", "-s", "flutter").splitlines()
            if "QA_EVENT" in line or "QA_STEP" in line
        ]
        for line in lines[seen:]:
            if "QA_STEP" in line:
                note("step", line=line.split("QA_STEP", 1)[1].strip()[:200])
                continue
            event = line.split("QA_EVENT", 1)[1].strip()
            note("event", ev=event)
            if event == "share":
                time.sleep(1.5)
                out = sh(
                    "shell",
                    f"am start -a android.intent.action.SEND -t text/plain "
                    f"--es android.intent.extra.TEXT '{SHARE_TEXT}' -n {PKG}/{SHARE_ACTIVITY}",
                )
                note("share-sent", result=(out.strip().splitlines() or [""])[-1])
            else:
                expect = event
        seen = len(lines)
        current = focus()
        if "printspooler" in current.lower() and expect:
            time.sleep(5)
            count += 1
            note(
                "system-print",
                expect=expect,
                focus=current,
                screenshot=shot(f"sys-{count:02d}-{expect.replace(' ', '-')}"),
            )
            back_until(lambda: "printspooler" not in focus().lower(), 3)
            note("back", focus=focus())
            expect = None
        elif any(name in current.lower() for name in BROWSERS) and PKG not in current and expect:
            time.sleep(4)
            count += 1
            urls = re.findall(
                r"dat=(https?://[^ }]+)", sh("shell", "dumpsys", "activity", "activities")
            )
            url = next((u for u in urls if "ecomsbd" in u), urls[0] if urls else "?")
            note(
                "browser",
                expect=expect,
                focus=current,
                url=url,
                screenshot=shot(f"sys-{count:02d}-{expect.replace(' ', '-')}"),
            )
            back_until(lambda: PKG in focus(), 4)
            note("back", focus=focus())
            expect = None
        time.sleep(1)
    note("stop")


if __name__ == "__main__":
    main()
