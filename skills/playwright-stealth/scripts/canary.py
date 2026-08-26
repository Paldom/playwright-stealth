#!/usr/bin/env python3
"""Verify a stealth identity against its own fingerprint baseline.

This is DRIFT DETECTION, not proof of invisibility. On the first run it writes
`baseline.json`; on every later run it diffs against it. The question it answers is
"is this still the same un-tampered device I logged in with?" — the thing that decides
whether a hand-made login keeps working.

Everything runs on `about:blank` inside the identity's own profile. The canary never
navigates to a target site, never touches a page you are working in, and deletes the one
property it installs. Passing it means no obvious local tell; it says nothing about
whether a live commercial wall will let you through.

REQUIRED (exit 1 on failure)
  navigator.webdriver is false AND its getter still reads as native code
  no cdc_ / __playwright / __pwInitScripts / _selenium automation globals
  canvas hash identical across two draws in the same session (a jittering hash is
    itself the tell) and identical to the baseline (the live proof the seed is holding)
  WebGL renderer is not a software rasterizer
  wheel input arrives as isTrusted

ADVISORY (reported, and fail the run only under --strict)
  Client-Hints coherence, UA vs navigator.platform, screen and hardware drift,
  window.chrome present, plugin list non-empty, persistent-cookie storage works,
  and — under --check-egress — the browser clock against the real exit IP

NOT checked here: whether you are still logged in anywhere. This launches its own browser,
so session cookies from the previous run are already gone before it can look, and it never
visits a target site. launch.py warns about session-only logins at the end of a live
session, which is the only moment the evidence still exists.

Usage:
  python3 canary.py --identity acme-portal            # write or diff the baseline
  python3 canary.py --identity acme-portal --json     # machine-readable scorecard
  python3 canary.py --identity acme-portal --check-egress  # + verify tz vs the exit IP
  python3 canary.py --identity acme-portal --rebaseline   # accept a reviewed change
  python3 canary.py --self-test                       # scoring logic only, no browser

Exit codes: 0 = every required check passed, 1 = a required check failed, 2 = error.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from launch import (  # noqa: E402  - deliberate: the launcher owns the identity contract
    BASELINE_NAME,
    PreflightError,
    identity_dir,
    open_identity,
    read_profile,
)

SOFTWARE_GPU = re.compile(r"swiftshader|llvmpipe|software|basic render|microsoft basic", re.I)
AUTOMATION_KEY = re.compile(
    r"cdc_|__playwright|__pwInitScripts|_selenium|__selenium|__webdriver|__driver|"
    r"_phantom|callPhantom|domAutomation", re.I)
MARKER_COOKIE = "stealth_canary_marker"
MARKER_URL = "https://example.com/"
# Only fetched under --check-egress. Returns {ip, country, city, timezone, ...}, which is
# what lets us compare the browser's clock against the IP the site actually sees.
EGRESS_URL = "https://ipinfo.io/json"
# Fields compared against the baseline. Only canvasHash is a required match; the rest
# are advisory because a legitimate OS or display change moves them.
DRIFT_FIELDS = ("canvasHash", "webglVendor", "webglRenderer", "userAgent", "platform",
                "languages", "hardwareConcurrency", "deviceMemory", "timezone", "screen")

PROBE_JS = r"""
() => {
  const out = {};
  out.webdriver = navigator.webdriver;
  try {
    const d = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    // A JS-patched getter stringifies to its source; a real one to [native code].
    out.webdriverGetterNative = !!(d && d.get && /\{\s*\[native code\]\s*\}/.test(
      Function.prototype.toString.call(d.get)));
  } catch (e) { out.webdriverGetterNative = null; }
  out.automationGlobals = Object.getOwnPropertyNames(window).filter(k =>
    /cdc_|__playwright|__pwInitScripts|_selenium|__selenium|__webdriver|__driver|_phantom|callPhantom|domAutomation/i.test(k));
  out.userAgent = navigator.userAgent;
  out.platform = navigator.platform || "";
  out.languages = navigator.languages || [];
  out.hardwareConcurrency = navigator.hardwareConcurrency || 0;
  out.deviceMemory = navigator.deviceMemory || 0;
  out.hasWindowChrome = !!(window.chrome && typeof window.chrome === 'object');
  out.pluginCount = (navigator.plugins && navigator.plugins.length) || 0;
  try { out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone; }
  catch (e) { out.timezone = null; }
  out.screen = {width: screen.width, height: screen.height, availWidth: screen.availWidth,
                availHeight: screen.availHeight, colorDepth: screen.colorDepth,
                devicePixelRatio: window.devicePixelRatio};
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null;
    out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
  } catch (e) { out.webglVendor = null; out.webglRenderer = null; }
  const draw = () => {
    const c = document.createElement('canvas'); c.width = 220; c.height = 40;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top'; ctx.font = "14px 'Arial'";
    ctx.fillStyle = '#f60'; ctx.fillRect(8, 8, 120, 20);
    ctx.fillStyle = '#069'; ctx.fillText('stealth-canary-✓', 10, 10);
    return c.toDataURL();
  };
  out.canvasA = draw();
  out.canvasB = draw();
  return out;                 // wheel isTrusted is measured separately, see measure_wheel
}
"""

# Keys score()/fingerprint_of() read that must be produced by PROBE_JS. Asserted by the
# self-test so a future edit cannot silently desync the probe from the scorer.
PROBE_KEYS = ("webdriver", "webdriverGetterNative", "automationGlobals", "userAgent",
              "platform", "languages", "hardwareConcurrency", "deviceMemory",
              "hasWindowChrome", "pluginCount", "timezone", "screen", "webglVendor",
              "webglRenderer", "canvasA", "canvasB")

HINTS_JS = r"""
async () => {
  if (!navigator.userAgentData) return null;
  try {
    return await navigator.userAgentData.getHighEntropyValues(
      ['platform', 'platformVersion', 'architecture', 'model', 'uaFullVersion', 'bitness']);
  } catch (e) { return {error: String(e)}; }
}
"""


def fingerprint_of(raw: dict) -> dict:
    """The stable subset of a probe, as it is stored in baseline.json."""
    return {
        "canvasHash": hashlib.sha256((raw.get("canvasA") or "").encode()).hexdigest()[:32],
        "webglVendor": raw.get("webglVendor"),
        "webglRenderer": raw.get("webglRenderer"),
        "userAgent": raw.get("userAgent"),
        "platform": raw.get("platform"),
        "languages": list(raw.get("languages") or []),
        "hardwareConcurrency": raw.get("hardwareConcurrency"),
        "deviceMemory": raw.get("deviceMemory"),
        "timezone": raw.get("timezone"),
        "screen": raw.get("screen"),
    }


def score(raw: dict, baseline: dict | None, versions: dict, strict: bool = False
          ) -> tuple[list[dict], dict, bool]:
    """Pure scoring: raw probe + baseline -> (results, fingerprint, ok). No I/O."""
    results: list[dict] = []

    def add(name: str, level: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "level": level, "detail": detail,
                        "status": "PASS" if ok else ("FAIL" if level == "required" else "WARN")})

    fp = fingerprint_of(raw)
    base_fp = (baseline or {}).get("fingerprint") or {}

    # --- required ---------------------------------------------------------------
    add("navigator.webdriver is false", "required", raw.get("webdriver") is False,
        f"value={raw.get('webdriver')!r}")
    native = raw.get("webdriverGetterNative")
    add("webdriver getter is native", "required", native is True,
        "reads [native code]" if native else
        f"getter is JS-patched or unreadable ({native!r}) — a patched getter is a stronger "
        "tell than the original value")
    globs = raw.get("automationGlobals") or []
    add("no automation globals", "required", not globs, f"found={globs}")

    stable = bool(raw.get("canvasA")) and raw.get("canvasA") == raw.get("canvasB")
    add("canvas hash stable in-session", "required", stable,
        "identical across two draws" if stable else
        "two draws of the same scene differ — a per-call noise injector is active, which "
        "is itself abnormal for Chromium")

    if not base_fp:
        add("canvas hash matches baseline", "advisory", True,
            "no baseline in effect — this run becomes the reference")
    else:
        matches = fp["canvasHash"] == base_fp.get("canvasHash")
        if matches:
            add("canvas hash matches baseline", "required", True, fp["canvasHash"])
        else:
            changed = [k for k in ("wrapper", "binary")
                       if versions.get(k) and (baseline or {}).get(k) and versions[k] != baseline[k]]
            hint = (f"{'/'.join(changed)} version changed "
                    f"({', '.join(f'{k}: {baseline[k]} -> {versions[k]}' for k in changed)}) "
                    "— a reviewed re-baseline is expected: re-run with --rebaseline"
                    if changed else
                    "with no version change this means the seed is NOT holding: check that "
                    "identity.seed is set and that launch goes through launch.py")
            add("canvas hash matches baseline", "required", False,
                f"{base_fp.get('canvasHash')} -> {fp['canvasHash']}; {hint}")

    gpu = raw.get("webglRenderer")
    add("WebGL renderer is hardware", "required", bool(gpu) and not SOFTWARE_GPU.search(gpu),
        gpu or "renderer unreadable")

    wheel = raw.get("wheelTrusted")
    if wheel is True or wheel is False:
        add("wheel input is trusted", "required", wheel is True, f"isTrusted={wheel}")
    else:
        # Never silently drop a required check: surface it loudly instead.
        add("wheel input measured", "advisory", False,
            f"could not measure wheel isTrusted ({wheel!r}) — re-run, and never scroll with "
            "JS scrollTo/scrollBy")

    # --- advisory ---------------------------------------------------------------
    ua = (raw.get("userAgent") or "")
    ua_l = ua.lower()
    hints = raw.get("clientHints")
    if isinstance(hints, dict) and not hints.get("error"):
        claimed = str(hints.get("platform") or "").lower()
        coherent = bool(claimed) and (claimed[:3] in ua_l or
                                      (claimed == "macos" and "mac os" in ua_l))
        add("Client Hints match the user agent", "advisory", coherent,
            f"UA-CH platform={hints.get('platform')!r} vs UA {ua[:60]!r}")
    elif hints is None:
        # navigator.userAgentData is secure-context only and about:blank has an opaque
        # origin, so the API is simply absent here. That is a property of where we
        # measure, not a browser defect — reporting it as a warning would make an
        # advisory that can never pass. The UA/platform check below covers the same
        # coherence question without needing a secure context.
        add("Client Hints", "advisory", True,
            "navigator.userAgentData not exposed on about:blank (non-secure context) — "
            "UA/platform coherence checked instead")
    else:
        add("Client Hints readable", "advisory", False,
            f"getHighEntropyValues threw -> {hints!r}")

    plat = (raw.get("platform") or "").lower()
    seam = (("windows" in ua_l or "win64" in ua_l) and not plat.startswith("win")) or \
           ("mac os" in ua_l and not plat.startswith("mac")) or \
           ("linux" in ua_l and "android" not in ua_l and not plat.startswith("linux"))
    add("user agent matches navigator.platform", "advisory", not seam,
        f"platform={raw.get('platform')!r} vs UA {ua[:60]!r}")

    add("window.chrome present", "advisory", bool(raw.get("hasWindowChrome")),
        str(raw.get("hasWindowChrome")))
    add("plugin list non-empty", "advisory", (raw.get("pluginCount") or 0) > 0,
        f"{raw.get('pluginCount')} plugins")
    add("hardwareConcurrency plausible", "advisory", (raw.get("hardwareConcurrency") or 0) > 0,
        str(raw.get("hardwareConcurrency")))

    # The check that actually catches a mis-set proxy. geoip=True derives the timezone
    # from a GeoIP database, and that database can simply be wrong: a real commodity
    # proxy's London exit IP resolved to America/Chicago, which is a louder tell than
    # any default value would have been. Only the live exit IP can falsify it.
    egress = raw.get("egress")
    if isinstance(egress, dict) and egress.get("timezone"):
        browser_tz = raw.get("timezone")
        add("browser timezone matches the real exit IP", "advisory",
            browser_tz == egress["timezone"],
            f"exit {egress.get('ip')} ({egress.get('country')}/{egress.get('city')}) is "
            f"{egress['timezone']}, browser says {browser_tz} — set network.timezone "
            f"explicitly to override geoip" if browser_tz != egress["timezone"] else
            f"{browser_tz} at exit {egress.get('ip')} ({egress.get('country')})")
    elif isinstance(egress, dict) and egress.get("error"):
        add("egress lookup", "advisory", False, str(egress["error"]))

    expected_tz = versions.get("expected_timezone")
    if expected_tz:
        add("timezone matches the configured egress", "advisory",
            raw.get("timezone") == expected_tz,
            f"browser={raw.get('timezone')!r} configured={expected_tz!r}")

    if base_fp:
        drifted = [k for k in DRIFT_FIELDS
                   if k != "canvasHash" and k in base_fp and base_fp[k] != fp[k]]
        add("no other fingerprint drift", "advisory", not drifted,
            "identical to baseline" if not drifted else
            "; ".join(f"{k}: {base_fp[k]!r} -> {fp[k]!r}" for k in drifted))
        marker = raw.get("markerCookie")
        # Deliberately narrow wording. This marker carries an explicit one-year expiry, so
        # it only ever proves the cookie FILE round-trips. It says nothing about whether
        # you are still signed in anywhere: most sites issue their auth token as a
        # session cookie (is_persistent=0), which Chromium drops on close by design. An
        # earlier label here read "marker cookie survived the last run", which was taken
        # as proof of a durable login and was not.
        add("persistent-cookie storage works", "advisory", marker is True,
            "the cookie file round-trips (NOT proof of a live login — session cookies "
            "still die on close)" if marker is True else
            f"marker missing ({marker!r}) — cookies are not persisting to user-data/ at all")

    # There is deliberately NO "logins survive a restart" row here. The canary launches its
    # own browser, so any session cookie from the previous run is already deleted before it
    # can look — the check would pass every time and read as "your logins are durable" when
    # it actually means "there is nothing left to see". launch.py runs it at the only
    # moment it can observe anything: at the end of a live session, before close.

    required_failed = any(r["status"] == "FAIL" for r in results)
    warned = any(r["status"] == "WARN" for r in results)
    return results, fp, not required_failed and (not warned if strict else True)


def probe_egress(ctx) -> dict:
    """Resolve the exit IP and its timezone through the browser's own network path.

    Opt-in (--check-egress) because it is the one measurement that leaves the machine.
    It goes to an IP-geo endpoint, never to a target site.
    """
    page = ctx.new_page()
    try:
        page.goto(EGRESS_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1500)
        data = json.loads(page.evaluate("() => document.body.innerText"))
        return {k: data.get(k) for k in ("ip", "country", "city", "timezone")}
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not fail the canary
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        with contextlib.suppress(Exception):
            page.close()


def probe(ctx, check_egress: bool = False) -> dict:
    """Run every browser-side measurement on about:blank. Never touches a target site."""
    page = ctx.new_page()
    try:
        page.goto("about:blank")
        raw = page.evaluate(PROBE_JS)
        raw["clientHints"] = page.evaluate(HINTS_JS)
        raw["wheelTrusted"] = measure_wheel(page)
        with_suppress(page, "() => { delete window.__stealthWheel; }")

        cookies = {c["name"] for c in ctx.cookies(MARKER_URL)}
        raw["markerCookie"] = MARKER_COOKIE in cookies
        if not raw["markerCookie"]:
            # Must carry an explicit expiry: a session cookie would vanish on close and
            # the durability check would then fail on every run for the wrong reason.
            ctx.add_cookies([{"name": MARKER_COOKIE, "value": "1", "url": MARKER_URL,
                              "expires": time.time() + 365 * 24 * 3600}])
    finally:
        page.close()
    if check_egress:
        raw["egress"] = probe_egress(ctx)
    return raw


def measure_wheel(page, attempts: int = 2):
    """True/False if the wheel event's isTrusted could be read, else "unmeasured".

    Retried once: a single missed dispatch is a timing hiccup, and an unmeasured wheel
    degrades a required check to an advisory warning, so it is worth one more try before
    accepting that. It is never silently dropped either way — score() reports it.
    """
    for attempt in range(attempts):
        try:
            page.evaluate("""() => {
              window.__stealthWheel = null;
              window.addEventListener('wheel', e => { window.__stealthWheel = e.isTrusted; },
                                      {once: true, passive: true});
            }""")
            page.mouse.move(200, 200)          # put the pointer over the viewport
            page.mouse.wheel(0, 120)
            page.wait_for_function("() => window.__stealthWheel !== null", timeout=5000)
            return page.evaluate(
                "() => { const v = window.__stealthWheel; delete window.__stealthWheel; return v; }")
        except Exception:  # noqa: BLE001 - retry once, then let score() surface it
            if attempt == attempts - 1:
                return "unmeasured"
    return "unmeasured"  # pragma: no cover - unreachable, keeps the return type honest


def with_suppress(page, js: str) -> None:
    try:
        page.evaluate(js)
    except Exception:  # noqa: BLE001 - best-effort cleanup only
        pass


def versions_now(cfg: dict) -> dict:
    from importlib import metadata
    out: dict = {"seed": (cfg.get("identity") or {}).get("seed")}
    try:
        out["wrapper"] = metadata.version("cloakbrowser")
    except Exception:  # noqa: BLE001
        out["wrapper"] = None
    try:
        from cloakbrowser import binary_info
        out["binary"] = (binary_info() or {}).get("version")
    except Exception:  # noqa: BLE001
        out["binary"] = None
    tz = ((cfg.get("network") or {}).get("timezone") or "").strip()
    if tz:
        out["expected_timezone"] = tz
    return out


def write_baseline(path: str, fp: dict, versions: dict) -> None:
    payload = {"seed": versions.get("seed"), "wrapper": versions.get("wrapper"),
               "binary": versions.get("binary"), "fingerprint": fp}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def self_test() -> int:
    clean = {
        "webdriver": False, "webdriverGetterNative": True, "automationGlobals": [],
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/150.0.0.0",
        "platform": "MacIntel", "languages": ["en-US", "en"], "hardwareConcurrency": 8,
        "deviceMemory": 8, "hasWindowChrome": True, "pluginCount": 5,
        "timezone": "Europe/Budapest",
        "screen": {"width": 1440, "height": 900, "devicePixelRatio": 2},
        "webglVendor": "Apple", "webglRenderer": "Apple M1 Pro",
        "canvasA": "data:image/png;base64,AAA", "canvasB": "data:image/png;base64,AAA",
        "wheelTrusted": True, "clientHints": {"platform": "macOS", "architecture": "arm"},
        "markerCookie": True,
    }
    versions = {"seed": 123, "wrapper": "0.5.8", "binary": "150.0.0.1"}

    res, fp, ok = score(clean, None, versions)
    assert ok, res
    assert not any(r["status"] == "FAIL" for r in res)
    # First run must not fail for lack of a baseline, and must say it is the reference.
    assert any(r["check"] == "canvas hash matches baseline" and r["level"] == "advisory"
               for r in res)

    baseline = {"seed": 123, "wrapper": "0.5.8", "binary": "150.0.0.1", "fingerprint": fp}
    res, _, ok = score(clean, baseline, versions)
    assert ok and any(r["check"] == "canvas hash matches baseline" and r["status"] == "PASS"
                      for r in res)

    # Seed not holding: same versions, different canvas -> required failure, blunt message.
    drifted = dict(clean, canvasA="data:image/png;base64,BBB", canvasB="data:image/png;base64,BBB")
    res, _, ok = score(drifted, baseline, versions)
    assert not ok
    msg = [r for r in res if r["check"] == "canvas hash matches baseline"][0]
    assert msg["status"] == "FAIL" and "seed is NOT holding" in msg["detail"]

    # Same drift after a binary upgrade: still a required failure, but points at --rebaseline.
    res, _, ok = score(drifted, baseline, dict(versions, binary="151.0.0.1"))
    assert not ok
    msg = [r for r in res if r["check"] == "canvas hash matches baseline"][0]
    assert "--rebaseline" in msg["detail"] and "150.0.0.1 -> 151.0.0.1" in msg["detail"]

    # A jittering canvas fails even with no baseline at all.
    _, _, ok = score(dict(clean, canvasB="data:image/png;base64,ZZZ"), None, versions)
    assert not ok

    dirty = dict(clean, webdriver=True, webdriverGetterNative=False,
                 automationGlobals=["cdc_asdjflasutopfhvcZLmcfl_"],
                 webglRenderer="Google SwiftShader", wheelTrusted=False)
    res, _, ok = score(dirty, baseline, versions)
    assert not ok
    fails = {r["check"] for r in res if r["status"] == "FAIL"}
    assert {"navigator.webdriver is false", "webdriver getter is native",
            "no automation globals", "WebGL renderer is hardware",
            "wheel input is trusted"} <= fails

    # An unmeasured wheel is advisory but must be visible, and never a silent skip.
    res, _, ok = score(dict(clean, wheelTrusted="unmeasured"), baseline, versions)
    assert ok
    assert any(r["check"] == "wheel input measured" and r["status"] == "WARN" for r in res)
    assert not any(r["check"] == "wheel input is trusted" for r in res)
    _, _, strict_ok = score(dict(clean, wheelTrusted="unmeasured"), baseline, versions, strict=True)
    assert not strict_ok

    # Advisory drift warns but does not fail; --strict escalates it.
    moved = dict(clean, hardwareConcurrency=16)
    res, _, ok = score(moved, baseline, versions)
    assert ok and any(r["check"] == "no other fingerprint drift" and r["status"] == "WARN"
                      for r in res)
    _, _, ok_strict = score(moved, baseline, versions, strict=True)
    assert not ok_strict

    # Timezone coherence is only judged when the profile asks for a specific zone.
    res, _, _ = score(clean, baseline, dict(versions, expected_timezone="America/New_York"))
    assert any(r["check"] == "timezone matches the configured egress" and r["status"] == "WARN"
               for r in res)

    # navigator.userAgentData is secure-context only, so it is ALWAYS absent on
    # about:blank. That must read as an informational pass, not a permanent warning
    # (found by running the canary against a real browser, where it warned every time).
    res, _, ok = score(dict(clean, clientHints=None), baseline, versions, strict=True)
    assert ok, [r for r in res if r["status"] != "PASS"]
    assert any(r["check"] == "Client Hints" and r["status"] == "PASS" for r in res)
    # An API that is present but throws is a different thing and still warns.
    res, _, ok = score(dict(clean, clientHints={"error": "NotAllowedError"}), baseline, versions)
    assert ok and any(r["check"] == "Client Hints readable" and r["status"] == "WARN"
                      for r in res)

    # Egress coherence. This is the check that caught a real commodity proxy whose
    # London exit IP had a geoip lookup setting the browser clock to America/Chicago.
    seam = dict(clean, timezone="America/Chicago",
                egress={"ip": "203.0.113.47", "country": "GB", "city": "London",
                        "timezone": "Europe/London"})
    res, _, ok = score(seam, baseline, versions)
    assert ok, "an egress mismatch is advisory, not a hard failure"
    row = [r for r in res if r["check"] == "browser timezone matches the real exit IP"][0]
    assert row["status"] == "WARN" and "America/Chicago" in row["detail"]
    _, _, ok_strict = score(seam, baseline, versions, strict=True)
    assert not ok_strict
    matched = dict(clean, timezone="Europe/London",
                   egress={"ip": "203.0.113.47", "country": "GB", "city": "London",
                           "timezone": "Europe/London"})
    res, _, _ = score(matched, baseline, versions)
    assert any(r["check"] == "browser timezone matches the real exit IP" and
               r["status"] == "PASS" for r in res)
    # A failed lookup is reported, never silently treated as agreement.
    res, _, _ = score(dict(clean, egress={"error": "TimeoutError"}), baseline, versions)
    assert any(r["check"] == "egress lookup" and r["status"] == "WARN" for r in res)
    # Without --check-egress there is no egress row at all (default stays offline).
    res, _, _ = score(clean, baseline, versions)
    assert not any("exit IP" in r["check"] for r in res)

    # UA/platform coherence works without a secure context, and catches a real seam.
    res, _, _ = score(dict(clean, userAgent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                           platform="MacIntel"), baseline, versions)
    assert any(r["check"] == "user agent matches navigator.platform" and r["status"] == "WARN"
               for r in res)
    res, _, _ = score(clean, baseline, versions)
    assert any(r["check"] == "user agent matches navigator.platform" and r["status"] == "PASS"
               for r in res)

    # Login durability. The marker only ever proves the cookie FILE round-trips, so its
    # wording must not imply a live session — that overclaim is what made a logged-out
    # profile read as healthy. The session-only-host check is the real signal.
    res, _, ok = score(clean, baseline, versions, strict=True)
    assert ok
    marker_row = [r for r in res if r["check"] == "persistent-cookie storage works"][0]
    assert "NOT proof of a live login" in marker_row["detail"]
    assert not any(r["check"].startswith("marker cookie survived") for r in res)
    # The durability row belongs to launch.py, not here — see the comment in score().
    assert not any(r["check"] == "logins survive a restart" for r in res)

    assert fingerprint_of(clean)["canvasHash"] == fingerprint_of(clean)["canvasHash"]
    assert AUTOMATION_KEY.search("__pwInitScripts") and not AUTOMATION_KEY.search("chrome")

    # The scorer and the browser-side probe must stay in step: every key the scorer reads
    # has to be one PROBE_JS actually sets, and the sample above must cover them all.
    for key in PROBE_KEYS:
        assert f"out.{key} =" in PROBE_JS, f"PROBE_JS never sets {key}"
        assert key in clean, f"self-test sample is missing {key}"
    assert "__stealthWheel" not in PROBE_JS, "wheel measurement belongs to measure_wheel()"

    print("self-test: ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--identity", help="identity name under .stealth/")
    ap.add_argument("--root", default=".", help="project root holding .stealth/ (default: cwd)")
    ap.add_argument("--rebaseline", action="store_true",
                    help="overwrite baseline.json with this run (use after a reviewed upgrade)")
    ap.add_argument("--check-egress", action="store_true",
                    help="also resolve the live exit IP and compare its timezone to the "
                         "browser's (the check that catches a wrong geoip lookup)")
    ap.add_argument("--strict", action="store_true", help="advisory warnings also fail the run")
    ap.add_argument("--json", action="store_true", help="emit the scorecard as JSON")
    ap.add_argument("--self-test", action="store_true", help="scoring logic only, no browser")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.identity:
        ap.error("--identity is required (or use --self-test)")

    try:
        idir = identity_dir(args.identity, args.root)
        cfg = read_profile(os.path.join(idir, "profile.toml"))
        versions = versions_now(cfg)
        baseline_path = os.path.join(idir, BASELINE_NAME)
        baseline = None
        if os.path.isfile(baseline_path) and not args.rebaseline:
            with open(baseline_path, encoding="utf-8") as fh:
                baseline = json.load(fh)
        with open_identity(args.identity, args.root) as ctx:
            raw = probe(ctx, check_egress=args.check_egress)
    except PreflightError as exc:
        print(f"PREFLIGHT: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    except Exception as exc:  # noqa: BLE001 - surface any probe failure to the agent
        print(f"CANARY-ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("RESULT: error")
        return 2

    results, fp, ok = score(raw, baseline, versions, strict=args.strict)
    if baseline is None and ok:
        write_baseline(baseline_path, fp, versions)
        print(f"STATUS baseline: {'rewritten' if args.rebaseline else 'written'} {baseline_path}")

    if args.json:
        print(json.dumps({"results": results, "fingerprint": fp, "versions": versions,
                          "ok": ok}, indent=2, default=str))
    else:
        for r in results:
            print(f"[{r['status']:4}] {r['level']:8} {r['check']} — {r['detail']}")
    print(f"RESULT: {'ok' if ok else 'fail'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
