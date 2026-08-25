#!/usr/bin/env python3
"""Check (and optionally install) the CloakBrowser stealth-Chromium toolchain.

Idempotent: probes what is present, and with --install adds only what is missing.
Prints machine-readable STATUS lines and a final RESULT so an agent can self-correct.

The authoritative probe is `python -m cloakbrowser info --json`, which reports the
binary that will ACTUALLY launch for this platform and licence. Note it exits 0 even
when nothing is installed, so this script reads the JSON, never the exit code.

Components
  REQUIRED  python >= 3.11          (the profile format is read with stdlib tomllib)
  REQUIRED  cloakbrowser pip package
  REQUIRED  the patched Chromium binary (~200 MB, auto-downloaded)
  REQUIRED  a passing launch test    (proves the binary actually executes)
  OPTIONAL  the [geoip] extra        (only needed to derive timezone/locale from a proxy)

Exit codes: 0 = all REQUIRED present, 1 = a required component missing, 2 = usage error.

Usage:
  python3 setup_check.py                    # probe only, no changes
  python3 setup_check.py --json             # same, machine-readable
  python3 setup_check.py --install          # install/download whatever is missing
  python3 setup_check.py --fix-quarantine   # macOS only: clear Gatekeeper quarantine
  python3 setup_check.py --self-test        # scoring logic only, no network, no browser
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import subprocess
import sys

PY = sys.executable
MIN_PYTHON = (3, 11)
# `info` runs a real launch test; the first `install` pulls ~200 MB.
INFO_TIMEOUT = 300
INSTALL_TIMEOUT = 1800


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def last_line(text: str, fallback: str) -> str:
    """Last non-empty line of `text`, or `fallback`. Safe on empty/whitespace input."""
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else fallback


def cloakbrowser_info(quick: bool) -> tuple[dict | None, str]:
    """Return (diagnostics, error). `quick` skips the launch test and all network."""
    cmd = [PY, "-m", "cloakbrowser", "info", "--json"] + (["--quick"] if quick else [])
    r = run(cmd, INFO_TIMEOUT)
    if r.returncode != 0:
        return None, last_line(r.stderr or r.stdout, f"cloakbrowser info exited {r.returncode}")
    try:
        return json.loads(r.stdout), ""
    except ValueError as exc:
        return None, f"cloakbrowser info emitted unparseable JSON: {exc}"


def clear_quarantine() -> tuple[bool, str]:
    """Clear the macOS Gatekeeper quarantine flag on the downloaded app bundle.

    The binary is ad-hoc signed, so macOS quarantines it and the first launch fails
    with "app is damaged". Scoped to the user's own ~/.cloakbrowser cache.
    """
    if platform.system() != "Darwin":
        return False, "not macOS — nothing to do"
    pattern = os.path.expanduser("~/.cloakbrowser/chromium-*/Chromium.app")
    bundles = glob.glob(pattern)
    if not bundles:
        return False, f"no app bundle found at {pattern}"
    failures = []
    for bundle in bundles:
        r = run(["xattr", "-cr", bundle], 120)
        if r.returncode != 0:
            failures.append(f"{bundle}: {last_line(r.stderr, 'xattr failed')}")
    if failures:
        return False, "; ".join(failures)
    return True, f"cleared quarantine on {len(bundles)} bundle(s)"


def evaluate(py_version: tuple[int, int], pkg_version: str | None, diag: dict | None,
             info_error: str) -> tuple[list[dict], bool]:
    """Pure scoring: map raw probes to (status rows, all_required_ok). No I/O."""
    rows: list[dict] = []

    def add(component: str, state: str, detail: str = "", required: bool = True) -> None:
        rows.append({"component": component, "state": state, "detail": detail,
                     "required": required})

    ok = True

    if py_version >= MIN_PYTHON:
        add("python", "OK", ".".join(map(str, py_version)))
    else:
        add("python", "FAIL",
            f"{'.'.join(map(str, py_version))} < {'.'.join(map(str, MIN_PYTHON))}"
            " — profile.toml is read with stdlib tomllib")
        ok = False

    if pkg_version:
        add("cloakbrowser_pkg", "OK", pkg_version)
    else:
        add("cloakbrowser_pkg", "MISSING",
            "run with --install, or: pip install -U 'cloakbrowser[geoip]'")
        ok = False

    if diag is None:
        add("binary", "SKIP", info_error or "cloakbrowser info unavailable")
        add("launch", "SKIP", "binary not probed")
        add("geoip_extra", "SKIP", "cloakbrowser info unavailable", required=False)
        return rows, False

    binary = diag.get("binary") or {}
    lic = (diag.get("license") or {}).get("tier", "unknown")
    version = binary.get("version") or "?"
    if binary.get("error"):
        add("binary", "FAIL", str(binary["error"]))
        ok = False
    elif binary.get("installed"):
        add("binary", "OK", f"chromium {version} (licence tier: {lic}) at {binary.get('path')}")
    else:
        add("binary", "MISSING",
            f"chromium {version} would be used but is not downloaded (licence tier: {lic})"
            " — run with --install")
        ok = False

    launch = diag.get("launch") or {}
    if launch.get("tested") and launch.get("ok"):
        add("launch", "OK", f"binary executed, reports {launch.get('version') or '?'}")
    elif launch.get("tested"):
        detail = str(launch.get("error") or "launch test failed")
        if launch.get("missing_libs"):
            detail += f" (missing libs: {launch['missing_libs']})"
        add("launch", "FAIL", detail)
        ok = False
    else:
        # SKIP is deliberately not a pass. An unproven binary must never report
        # RESULT: ok, or --quick becomes a way to claim a verified install without
        # ever executing the binary.
        add("launch", "SKIP", f"{launch.get('reason') or 'not tested'} — not a pass; "
                              "re-run without --quick to verify the binary executes")
        ok = False

    has_geoip = bool((diag.get("modules") or {}).get("geoip2"))
    add("geoip_extra", "OK" if has_geoip else "WARN",
        "" if has_geoip else "install 'cloakbrowser[geoip]' to derive timezone/locale from a proxy",
        required=False)

    return rows, ok


def probe(quick: bool) -> tuple[list[dict], bool, dict | None]:
    from importlib import metadata
    try:
        pkg_version = metadata.version("cloakbrowser")
    except metadata.PackageNotFoundError:
        pkg_version = None
    diag, info_error = (None, "cloakbrowser not installed") if not pkg_version \
        else cloakbrowser_info(quick=quick)
    rows, ok = evaluate(sys.version_info[:2], pkg_version, diag, info_error)
    return rows, ok, diag


def install_missing(rows: list[dict]) -> None:
    """Install only what the probe reported as absent. Prints its own STATUS lines."""
    states = {r["component"]: r["state"] for r in rows}

    if states.get("cloakbrowser_pkg") in ("MISSING", "FAIL"):
        emit("cloakbrowser_pkg", "INSTALLING")
        r = run([PY, "-m", "pip", "install", "-U", "cloakbrowser[geoip]"], INSTALL_TIMEOUT)
        if r.returncode != 0:
            emit("cloakbrowser_pkg", "FAIL", last_line(r.stderr, "pip install failed"))
            return
    elif states.get("geoip_extra") == "WARN":
        emit("geoip_extra", "INSTALLING")
        run([PY, "-m", "pip", "install", "-U", "cloakbrowser[geoip]"], INSTALL_TIMEOUT)

    if states.get("binary") in ("MISSING", "FAIL", "SKIP") or states.get("launch") != "OK":
        emit("binary", "DOWNLOADING", "~200 MB on first run")
        r = run([PY, "-m", "cloakbrowser", "install"], INSTALL_TIMEOUT)
        if r.returncode != 0:
            emit("binary", "FAIL", last_line(r.stderr or r.stdout, "cloakbrowser install failed"))
            return
        if platform.system() == "Darwin":
            done, detail = clear_quarantine()
            emit("quarantine", "OK" if done else "SKIP", detail)


def emit(component: str, state: str, detail: str = "") -> None:
    print(f"STATUS {component}: {state}{(' ' + detail) if detail else ''}")


def self_test() -> int:
    good = {
        "binary": {"installed": True, "version": "150.0.1.2", "path": "/x/Chromium"},
        "launch": {"tested": True, "ok": True, "version": "150.0.1.2"},
        "license": {"tier": "pro"},
        "modules": {"geoip2": True},
    }
    rows, ok = evaluate((3, 12), "0.5.8", good, "")
    assert ok, rows
    assert all(r["state"] == "OK" for r in rows), rows

    # --quick skips the launch test, and a skipped test is never a pass: a binary that
    # has not been executed is not a verified install.
    quick = dict(good, launch={"tested": False, "reason": "skipped (--quick)"})
    rows, ok = evaluate((3, 12), "0.5.8", quick, "")
    assert not ok
    launch_row = [r for r in rows if r["component"] == "launch"][0]
    assert launch_row["state"] == "SKIP" and "not a pass" in launch_row["detail"]

    # `info` exits 0 with nothing installed — the gate must still fail.
    empty = {
        "binary": {"installed": False, "version": "145.0.1.2", "path": "/x/Chromium"},
        "launch": {"tested": False, "reason": "binary not installed"},
        "license": {"tier": "free"},
        "modules": {"geoip2": False},
    }
    rows, ok = evaluate((3, 12), "0.5.8", empty, "")
    assert not ok
    assert {r["component"]: r["state"] for r in rows}["binary"] == "MISSING"

    # A missing geoip extra is advisory and must never fail the run on its own.
    rows, ok = evaluate((3, 12), "0.5.8", dict(good, modules={"geoip2": False}), "")
    assert ok
    assert any(r["component"] == "geoip_extra" and r["state"] == "WARN" for r in rows)

    # Too-old Python fails even when everything else is perfect.
    _, ok = evaluate((3, 10), "0.5.8", good, "")
    assert not ok

    # A failing launch test fails the run even though the binary is on disk.
    broken = dict(good, launch={"tested": True, "ok": False, "error": "dyld: symbol not found"})
    rows, ok = evaluate((3, 12), "0.5.8", broken, "")
    assert not ok
    assert any(r["component"] == "launch" and r["state"] == "FAIL" for r in rows)

    # No package at all: nothing crashes, everything required is unmet.
    rows, ok = evaluate((3, 12), None, None, "cloakbrowser not installed")
    assert not ok and len(rows) == 5

    assert last_line("\n  \n", "fallback") == "fallback"
    assert last_line("a\nb\n", "fallback") == "b"

    print("self-test: ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="install/download missing components")
    ap.add_argument("--quick", action="store_true",
                    help="skip the launch test (fast + offline, but can never RESULT: ok)")
    ap.add_argument("--fix-quarantine", action="store_true",
                    help="macOS: clear the Gatekeeper quarantine flag on the downloaded binary")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--self-test", action="store_true", help="scoring logic only, no network")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.fix_quarantine:
        done, detail = clear_quarantine()
        emit("quarantine", "OK" if done else "FAIL", detail)
        if not done:
            print("RESULT: fail")
            return 1

    rows, ok, diag = probe(quick=args.quick)
    if args.install and not ok:
        for row in rows:
            emit(row["component"], row["state"], row["detail"])
        install_missing(rows)
        rows, ok, diag = probe(quick=args.quick)

    if args.json:
        print(json.dumps({"components": rows, "ok": ok, "info": diag}, indent=2, default=str))
    else:
        for row in rows:
            emit(row["component"], row["state"], row["detail"])
    print(f"RESULT: {'ok' if ok else 'fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
