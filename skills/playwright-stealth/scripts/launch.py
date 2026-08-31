#!/usr/bin/env python3
"""Create, resume, inspect or hold open a pinned CloakBrowser stealth identity.

An identity is a directory in the consuming project, all of it gitignored:

    <project>/.stealth/<identity>/
        profile.toml    settings (hand-editable, read with stdlib tomllib)
        user-data/      CloakBrowser user_data_dir — cookies, saved logins, localStorage
        baseline.json   canary fingerprint baseline (written by canary.py)
        .lock           single-writer guard

RESUMING IS JUST RELAUNCHING with the same `user-data/` and the same
`--fingerprint=<seed>`. There is no export/import step. The seed is what keeps the
*device* constant: with no seed flag the binary rolls a fresh random fingerprint every
launch, so a site that fingerprints the session sees a new device each time.

Modes:
  --init          scaffold the identity, roll a unique seed, enforce the gitignore guard
  --check         preflight only (no launch), exit 0/1
  --print-args    show the exact launch kwargs this profile resolves to, then exit
  (default)       launch headful and hold the browser open until Enter / Ctrl-C
  --self-test     pure logic checks, no browser, no network

Agent code should not re-derive any of this — import the context manager instead:

    import os, sys
    sys.path.insert(0, os.path.join(os.environ["CLAUDE_SKILL_DIR"], "scripts"))
    from launch import open_identity

    with open_identity("acme-portal") as ctx:      # lock + seed + humanize handled
        page = ctx.new_page()
        page.goto("https://example.com")

Exit codes: 0 ok, 1 preflight/launch failure, 2 usage.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

STEALTH_DIR = ".stealth"
PROFILE_NAME = "profile.toml"
USER_DATA = "user-data"
LOCK_NAME = ".lock"
BASELINE_NAME = "baseline.json"
# The binary's own auto-generated seeds live in this range; stay inside it.
SEED_MIN, SEED_MAX = 10_000, 99_999
MIN_PYTHON = (3, 11)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "assets", "profile.toml.template")
STEALTH_README = """# .stealth/ — live browser credentials

Every directory in here is one browsing identity. `user-data/` holds decryptable
session cookies and saved passwords: it is full session-takeover material.

Never commit it, never copy it between machines, never share it, and never point two
processes at the same identity at once. One identity per account.
"""


class PreflightError(RuntimeError):
    """A launch precondition failed. The message is shown to the user verbatim."""


# --------------------------------------------------------------------------- paths


def identity_dir(identity: str, root: str) -> str:
    if not identity or "/" in identity or "\\" in identity or identity.startswith("."):
        raise PreflightError(
            f"invalid identity name {identity!r} — use a kebab-case directory name"
        )
    return os.path.join(os.path.abspath(root), STEALTH_DIR, identity)


# ---------------------------------------------------------------------- profile io


def read_profile(path: str) -> dict:
    import tomllib

    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise PreflightError(f"no {path} — run this script with --init first") from None
    except tomllib.TOMLDecodeError as exc:
        raise PreflightError(f"{path} is not valid TOML: {exc}") from None


def derive(cfg: dict) -> tuple[dict, list[str]]:
    """Map profile.toml onto launch_persistent_context kwargs.

    Side-effect free apart from reading STEALTH_PROXY_PASSWORD, which is deliberately the
    only channel for the proxy password. Returns (kwargs-without-user_data_dir, notes);
    `notes` explains every setting that was dropped, weakened or overridden, so the agent
    reports it instead of guessing.
    """
    browser = cfg.get("browser") or {}
    ident = cfg.get("identity") or {}
    net = cfg.get("network") or {}
    notes: list[str] = []

    seed = ident.get("seed") or 0
    # isinstance(True, int) is True in Python — a TOML `seed = true` would otherwise
    # sail through and produce `--fingerprint=True`.
    if not isinstance(seed, int) or isinstance(seed, bool) or seed <= 0:
        raise PreflightError(
            "identity.seed is unset (0). An unseeded launch rolls a NEW random device "
            "fingerprint every time, which is exactly what this skill exists to prevent. "
            "Run with --init to roll and persist a seed."
        )

    # stealth_args stays True: it carries the fingerprint flags. Our args win over the
    # wrapper's random default because build_args() dedups by flag key, ours last.
    args = [f"--fingerprint={seed}"]

    platform_claim = (ident.get("platform") or "").strip()
    if platform_claim:
        args.append(f"--fingerprint-platform={platform_claim}")
        notes.append(f"forcing platform={platform_claim}; coherence with the real host is on you")

    quota = ident.get("storage_quota_mb") or 0
    if isinstance(quota, int) and quota > 0:
        args.append(f"--fingerprint-storage-quota={quota}")
    else:
        notes.append(
            "storage quota left at the binary's normalized value — some detectors "
            "read that as incognito; set identity.storage_quota_mb to present as a "
            "regular profile"
        )

    kwargs: dict = {
        "headless": bool(browser.get("headless", False)),
        "humanize": bool(browser.get("humanize", True)),
        "args": args,
    }
    if kwargs["headless"]:
        notes.append("headless=true — headless leaks GPU/display/timing signals even when patched")
    if not kwargs["humanize"]:
        notes.append("humanize=false — mouse, keyboard and scroll will be machine-timed")

    preset = (browser.get("human_preset") or "").strip()
    if preset:
        if preset not in ("default", "careful"):
            raise PreflightError(
                f"browser.human_preset={preset!r} is not a valid preset — use 'default' or "
                "'careful', or leave it empty for the wrapper default"
            )
        kwargs["human_preset"] = preset

    proxy_server = (net.get("proxy_server") or "").strip()
    if proxy_server:
        proxy = {"server": proxy_server}
        username = (net.get("proxy_username") or "").strip()
        if username:
            proxy["username"] = username
            password = os.environ.get("STEALTH_PROXY_PASSWORD")
            if password:
                proxy["password"] = password
            else:
                notes.append(
                    "proxy_username is set but STEALTH_PROXY_PASSWORD is not exported "
                    "— the proxy will likely reject the connection"
                )
        bypass = (net.get("proxy_bypass") or "").strip()
        if bypass:
            proxy["bypass"] = bypass
        kwargs["proxy"] = proxy
        kwargs["geoip"] = bool(net.get("geoip", True))
        webrtc = (net.get("webrtc_ip") or "").strip()
        if webrtc:
            args.append(f"--fingerprint-webrtc-ip={webrtc}")
    else:
        # No proxy: geoip would resolve THIS machine's public IP through a third-party
        # echo service, and --fingerprint-webrtc-ip=auto would be dropped by the wrapper
        # anyway. The browser reports the host's real timezone/locale, which already
        # matches the host's own egress. Set timezone/locale explicitly if it does not
        # (VPN, travel) — the canary flags that mismatch. Passed explicitly rather than
        # left to the wrapper default so the "no third-party lookup" promise cannot be
        # broken by a future change of that default.
        kwargs["geoip"] = False
        notes.append(
            "no proxy — using this machine's own connection; geoip and webrtc_ip "
            "are not passed (no third-party lookup)"
        )

    timezone = (net.get("timezone") or "").strip()
    locale = (net.get("locale") or "").strip()
    if timezone:
        kwargs["timezone"] = timezone
    if locale:
        kwargs["locale"] = locale
    if (timezone or locale) and kwargs.get("geoip"):
        notes.append("explicit timezone/locale override geoip for the fields you set")

    return kwargs, notes


# ------------------------------------------------------------------------- locking


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return True
    return True


def chromium_singleton_holder(user_data_dir: str) -> int | None:
    """PID of a live Chromium already using this profile, via its own SingletonLock.

    Chromium writes SingletonLock as a symlink to `<hostname>-<pid>`. A dangling one is
    a stale lock from a crash; a live PID means a browser we did not start is attached.
    """
    link = os.path.join(user_data_dir, "SingletonLock")
    if not os.path.islink(link):
        return None
    try:
        pid = int(os.readlink(link).rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


@contextlib.contextmanager
def profile_lock(path: str, force: bool = False):
    """Single-writer guard. Opening one profile twice corrupts it.

    Uses an advisory flock where available: the kernel drops it when the process dies,
    so a crash can never leave a stale lock. Falls back to exclusive-create plus a
    PID-liveness check on platforms without fcntl.
    """
    fd = None
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-Unix
        fcntl = None

    if fcntl is not None:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise PreflightError(
                f"identity is already open by another process (lock held on {path}). "
                "A Chromium profile is single-writer — close the other session, or use a "
                "separate identity to run two tasks at once."
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    else:  # pragma: no cover - non-Unix
        if os.path.exists(path):
            if not force:
                try:
                    holder = int(Path(path).read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    holder = 0
                if holder and _pid_alive(holder):
                    raise PreflightError(
                        f"identity is already open by PID {holder} (lock file {path}). "
                        "Close it, or use a separate identity."
                    )
            os.unlink(path)  # stale, or an explicit --force
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.write(fd, f"{os.getpid()}\n".encode())
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        if fcntl is None:  # pragma: no cover - non-Unix
            # No kernel-released lock here, so the file itself IS the lock: remove it,
            # otherwise a recycled PID eventually reads as a live holder.
            with contextlib.suppress(OSError):
                os.unlink(path)


# ---------------------------------------------------------------------- preflight


def gitignore_problem(root: str) -> str | None:
    """None when `.stealth/` is safely ignored, else a message explaining why not."""
    root = os.path.abspath(root)

    def git(*argv) -> int | None:
        try:
            return subprocess.run(
                ["git", "-C", root, *argv], capture_output=True, text=True, timeout=30
            ).returncode
        except (OSError, subprocess.SubprocessError):
            return None  # git absent or hung — treat as "no repo to leak into"

    if git("rev-parse", "--git-dir") != 0:
        return None  # not a git repo, or no git at all — nothing to leak into
    if git("check-ignore", "-q", os.path.join(root, STEALTH_DIR, "probe")) == 0:
        return None
    return (
        f"{STEALTH_DIR}/ is NOT gitignored in {root}. It holds session cookies and "
        f"saved passwords. Add a line '{STEALTH_DIR}/' to .gitignore, then retry "
        "(--init does this for you)."
    )


def preflight_config(identity: str, root: str) -> tuple[str, dict]:
    """Everything checkable without a browser installed. Returns (dir, config)."""
    if sys.version_info[:2] < MIN_PYTHON:
        raise PreflightError(
            f"Python {'.'.join(map(str, MIN_PYTHON))}+ required (profile.toml is read with "
            f"stdlib tomllib); running {sys.version.split()[0]}"
        )
    idir = identity_dir(identity, root)
    if not os.path.isdir(idir):
        # Name the skill, not just the flag: the agent may have routed straight here and
        # needs to know provisioning lives elsewhere before it improvises a profile.
        raise PreflightError(
            f"no identity at {idir} — the playwright-stealth-identity skill creates it "
            f"(identity.py --identity {identity} --init). This skill does not create "
            "identities."
        )
    cfg = read_profile(os.path.join(idir, PROFILE_NAME))
    problem = gitignore_problem(root)
    if problem:
        raise PreflightError(problem)
    return idir, cfg


def preflight_runtime(idir: str) -> None:
    """Checks that only matter when a browser is actually about to start."""
    try:
        import cloakbrowser  # noqa: F401
    except ImportError as exc:
        raise PreflightError(
            f"cloakbrowser is not importable ({exc}) — run the playwright-stealth-setup "
            "skill first; this skill installs nothing"
        ) from None
    holder = chromium_singleton_holder(os.path.join(idir, USER_DATA))
    if holder:
        raise PreflightError(
            f"a Chromium process (PID {holder}) is already using {idir}/{USER_DATA}. "
            "Close it before launching — two writers corrupt the profile."
        )


# ---------------------------------------------------------------------------- init


def existing_seeds(root: str) -> set[int]:
    base = os.path.join(os.path.abspath(root), STEALTH_DIR)
    seeds: set[int] = set()
    if not os.path.isdir(base):
        return seeds
    for name in os.listdir(base):
        path = os.path.join(base, name, PROFILE_NAME)
        if not os.path.isfile(path):
            continue
        with contextlib.suppress(Exception):
            seeds.add(int(((read_profile(path).get("identity") or {}).get("seed")) or 0))
    return seeds


def roll_seed(taken: set[int]) -> int:
    """A seed no sibling identity already uses. Two identities sharing a seed share a
    device fingerprint, which links the accounts permanently."""
    for _ in range(1000):
        seed = SEED_MIN + secrets.randbelow(SEED_MAX - SEED_MIN + 1)
        if seed not in taken:
            return seed
    raise PreflightError("could not find an unused seed — you have far too many identities")


def ensure_gitignored(root: str) -> str:
    root = os.path.abspath(root)
    if gitignore_problem(root) is None:
        return "already ignored (or not a git repo)"
    path = os.path.join(root, ".gitignore")
    existing = ""
    if os.path.isfile(path):
        existing = Path(path).read_text(encoding="utf-8")
    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(
            f"{sep}\n# Stealth browsing identities — live session credentials.\n{STEALTH_DIR}/\n"
        )
    return f"appended '{STEALTH_DIR}/' to {path}"


def toml_value(value) -> str:
    """Render a Python value as the TOML scalar the template expects."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_settings(text: str, settings: dict) -> str:
    """Substitute values in the commented template, in place, one key at a time.

    Keeps every comment and the column alignment: the template is the single source of
    truth for defaults and explanation, and this never serialises TOML over the top of it.
    Every key in the template is unique, so a bare key anchor is unambiguous.
    """
    for key, value in settings.items():
        pattern = re.compile(
            rf'^(\s*{re.escape(key)}\s*=\s*)(?:"[^"]*"|[^\s#]+)(\s*(?:#.*)?)$', re.M
        )

        def replace(m, value=value):
            assigned = f"{m.group(1)}{toml_value(value)}"
            comment = m.group(2).lstrip()
            if not comment:
                return assigned
            # Re-pad so the trailing comment stays in its original column. A human
            # hand-edits this file; a value that shoves every comment out of line makes
            # it look machine-mangled.
            column = len(m.group(0)) - len(comment)
            return assigned + " " * max(1, column - len(assigned)) + comment

        text, n = pattern.subn(replace, text, 1)
        if n != 1:
            raise PreflightError(
                f"cannot set {key!r}: no such key in {TEMPLATE}. The template and this "
                "script have drifted apart — fix the template rather than the profile."
            )
    return text


def settings_from_args(args) -> dict:
    """Validated profile.toml overrides from the CLI. Empty dict = pure defaults."""
    settings: dict = {}
    if args.proxy is not None:
        proxy = args.proxy.strip()
        if proxy and not re.match(r"^(https?|socks5)://", proxy):
            raise PreflightError(
                f"--proxy {proxy!r} must start with http://, https:// or socks5:// "
                "(and must be CONNECT-only — a TLS-terminating proxy destroys the real "
                "Chrome JA3/JA4 fingerprint)"
            )
        settings["proxy_server"] = proxy
    if args.proxy_user is not None:
        settings["proxy_username"] = args.proxy_user.strip()
    if args.timezone is not None:
        tz = args.timezone.strip()
        if tz and "/" not in tz and tz != "UTC":
            raise PreflightError(
                f"--timezone {tz!r} does not look like an IANA zone (e.g. Europe/London)"
            )
        settings["timezone"] = tz
    if args.locale is not None:
        loc = args.locale.strip()
        if loc and not re.match(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$", loc):
            raise PreflightError(f"--locale {loc!r} does not look like a BCP-47 tag (e.g. en-US)")
        settings["locale"] = loc
    if args.human_preset is not None:
        settings["human_preset"] = args.human_preset
    if args.storage_quota_mb is not None:
        if args.storage_quota_mb < 0:
            raise PreflightError("--storage-quota-mb must be 0 or greater (0 = binary default)")
        settings["storage_quota_mb"] = args.storage_quota_mb
    return settings


def write_profile(profile: str, text: str) -> None:
    tmp = profile + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, profile)  # atomic: no half-written profile is ever readable


def init_identity(identity: str, root: str, settings: dict | None = None) -> int:
    idir = identity_dir(identity, root)
    base = os.path.dirname(idir)
    os.makedirs(idir, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    readme = os.path.join(base, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(STEALTH_README)
    print(f"STATUS gitignore: {ensure_gitignored(root)}")

    settings = settings or {}
    profile = os.path.join(idir, PROFILE_NAME)
    if os.path.exists(profile):
        seed = (read_profile(profile).get("identity") or {}).get("seed") or 0
        if seed:
            # Retune: apply the given settings to the EXISTING file, keeping its seed,
            # its comments, and any hand-edits to keys we were not asked to change.
            if settings:
                text = apply_settings(Path(profile).read_text(encoding="utf-8"), settings)
                write_profile(profile, text)
                for key, value in settings.items():
                    print(f"STATUS set: {key} = {toml_value(value)}")
            print(f"STATUS profile: exists, seed {seed} kept")
            print(f"STATUS identity: {idir}")
            print("RESULT: ok")
            return 0
        print("STATUS profile: exists but unseeded — rolling a seed")
    seed = roll_seed(existing_seeds(root))
    # Copy the commented template and substitute values into it. The template is the
    # single source of truth for defaults and comments; this never serialises TOML.
    text = Path(TEMPLATE).read_text(encoding="utf-8")
    if "seed = 0" not in text:
        raise PreflightError(f"template {TEMPLATE} has no 'seed = 0' line to substitute")
    text = apply_settings(text, {"seed": seed, **settings})
    write_profile(profile, text)
    os.makedirs(os.path.join(idir, USER_DATA), mode=0o700, exist_ok=True)
    print(f"STATUS profile: created {profile}")
    print(f"STATUS seed: {seed}")
    for key, value in settings.items():
        print(f"STATUS set: {key} = {toml_value(value)}")
    print(f"STATUS identity: {idir}")
    print("RESULT: ok")
    return 0


# -------------------------------------------------------------------------- launch


@contextlib.contextmanager
def open_identity(identity: str, root: str = ".", force: bool = False):
    """Yield a live Playwright BrowserContext for `identity`, locked for this process.

    Handles preflight, the single-writer lock, and the profile.toml derivation. This is
    the only supported way to drive an identity from code — deriving the launch kwargs
    by hand is how a session silently ends up unseeded or headless.
    """
    idir, cfg = preflight_config(identity, root)
    preflight_runtime(idir)
    kwargs, notes = derive(cfg)
    for note in notes:
        print(f"NOTE: {note}", file=sys.stderr)
    user_data_dir = os.path.join(idir, USER_DATA)
    os.makedirs(user_data_dir, mode=0o700, exist_ok=True)
    from cloakbrowser import launch_persistent_context

    with profile_lock(os.path.join(idir, LOCK_NAME), force=force):
        ctx = launch_persistent_context(user_data_dir, **kwargs)
        try:
            yield ctx
        finally:
            with contextlib.suppress(Exception):
                ctx.close()


def session_only_hosts(ctx) -> list[str]:
    """Hosts whose every cookie dies when the browser closes.

    Playwright reports a session cookie as ``expires == -1``. A host holding nothing but
    session cookies has no durable token, so a login there will NOT survive a restart
    however well the profile persists — the profile is intact and the login is gone. This
    is the failure a persistent profile looks like it should prevent and cannot.

    It has to be read from a LIVE context: once the browser closes, Chromium deletes those
    cookies and the evidence with them.
    """
    try:
        cookies = ctx.cookies()
    except Exception:
        return []
    durable, seen = set(), set()
    for c in cookies:
        host = (c.get("domain") or "").lstrip(".")
        if not host:
            continue
        seen.add(host)
        if (c.get("expires") or -1) > 0:
            durable.add(host)
    return sorted(seen - durable)


def warn_session_only(ctx) -> None:
    """Print the durability warning while the live cookies can still be inspected."""
    hosts = session_only_hosts(ctx)
    if not hosts:
        return
    print(
        f"NOTE: {len(hosts)} host(s) gave this profile only session-scoped cookies and "
        f"will be signed out when the browser closes: {', '.join(hosts[:6])}"
        f"{' …' if len(hosts) > 6 else ''}",
        file=sys.stderr,
    )
    print(
        'NOTE: if you just logged in to one of those, log in again and tick "remember '
        'me" / "keep me signed in" — otherwise the site issues a token that cannot '
        "outlive this window, no matter how well the profile persists.",
        file=sys.stderr,
    )


def run_canary(identity: str, root: str) -> int:
    script = os.path.join(SKILL_DIR, "scripts", "canary.py")
    proc = subprocess.run(
        [sys.executable, script, "--identity", identity, "--root", root], timeout=600
    )
    return proc.returncode


def hold_open(identity: str, root: str, url: str | None, force: bool) -> int:
    with open_identity(identity, root, force=force) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if url:
            page.goto(url)
        print(f"STATUS identity: {identity}")
        print(f"STATUS pages: {len(ctx.pages)}")
        print("RESULT: ok")
        print(
            "Browser open. Log in or work in the window, then press Enter (or Ctrl-C) "
            "to close and save the profile.",
            file=sys.stderr,
        )
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input()
        # Last chance to see this: the close below deletes every session cookie.
        warn_session_only(ctx)
    return 0


# ----------------------------------------------------------------------- self-test


def self_test() -> int:
    base = {
        "browser": {"headless": False, "humanize": True, "human_preset": "careful"},
        "identity": {"seed": 12345, "platform": "", "storage_quota_mb": 5000},
        "network": {
            "proxy_server": "",
            "geoip": True,
            "webrtc_ip": "auto",
            "timezone": "",
            "locale": "",
        },
    }

    kwargs, notes = derive(base)
    assert kwargs["headless"] is False and kwargs["humanize"] is True
    assert kwargs["human_preset"] == "careful"
    assert "--fingerprint=12345" in kwargs["args"]
    assert "--fingerprint-storage-quota=5000" in kwargs["args"]
    # No proxy: geoip is explicitly OFF and no webrtc flag is passed, so nothing
    # resolves this machine's public IP through a third-party echo service.
    assert kwargs["geoip"] is False and "proxy" not in kwargs
    assert not any(a.startswith("--fingerprint-webrtc-ip") for a in kwargs["args"])
    assert any("no proxy" in n for n in notes)

    # An unset seed must be a hard error, never a silent random identity.
    # `True` is included deliberately: isinstance(True, int) would otherwise let a TOML
    # `seed = true` through and emit `--fingerprint=True`.
    for bad in (0, None, -1, "42", True, 1.5):
        try:
            derive({**base, "identity": {**base["identity"], "seed": bad}})
        except PreflightError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"seed={bad!r} should have raised")

    proxied = {
        **base,
        "network": {
            "proxy_server": "http://p:8080",
            "proxy_username": "u",
            "geoip": True,
            "webrtc_ip": "auto",
            "timezone": "",
            "locale": "",
        },
    }
    kwargs, notes = derive(proxied)
    assert kwargs["proxy"]["server"] == "http://p:8080" and kwargs["geoip"] is True
    assert "--fingerprint-webrtc-ip=auto" in kwargs["args"]
    assert "password" not in kwargs["proxy"]  # not in the env → not invented
    assert any("STEALTH_PROXY_PASSWORD" in n for n in notes)

    os.environ["STEALTH_PROXY_PASSWORD"] = "s3cret"  # noqa: S105 - an environment-variable NAME, not a credential
    try:
        kwargs, _ = derive(proxied)
        assert kwargs["proxy"]["password"] == "s3cret"  # noqa: S105 - an environment-variable NAME, not a credential
    finally:
        del os.environ["STEALTH_PROXY_PASSWORD"]

    # Explicit timezone/locale become binary flags via the wrapper's own params.
    kwargs, notes = derive(
        {**base, "network": {**base["network"], "timezone": "Europe/Budapest", "locale": "hu-HU"}}
    )
    assert kwargs["timezone"] == "Europe/Budapest" and kwargs["locale"] == "hu-HU"

    # Weakened settings must be reported, not silently accepted.
    _, notes = derive({**base, "browser": {"headless": True, "humanize": False}})
    assert any("headless" in n for n in notes) and any("humanize" in n for n in notes)

    try:
        derive({**base, "browser": {**base["browser"], "human_preset": "turbo"}})
    except PreflightError as exc:
        assert "careful" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid human_preset should have raised")

    # A missing quota is a warning, never a failure.
    kwargs, notes = derive({**base, "identity": {"seed": 1, "storage_quota_mb": 0}})
    assert not any(a.startswith("--fingerprint-storage-quota") for a in kwargs["args"])
    assert any("incognito" in n for n in notes)

    # Session-cookie durability. A host with any persistent cookie is fine; one with only
    # session cookies is a login that will not survive the close.
    class FakeCtx:
        def __init__(self, cookies):
            self._c = cookies

        def cookies(self):
            return self._c

    assert session_only_hosts(FakeCtx([])) == []
    assert session_only_hosts(
        FakeCtx(
            [
                {"domain": "app.impact.com", "expires": -1},
                {"domain": ".dashboard.oxylabs.io", "expires": -1},
                {"domain": "dashboard.webshare.io", "expires": 1_900_000_000},
                {"domain": "mixed.example", "expires": -1},
                {"domain": "mixed.example", "expires": 1_900_000_000},
            ]
        )
    ) == ["app.impact.com", "dashboard.oxylabs.io"], "a leading dot must not split a host"

    class DeadCtx:
        def cookies(self):
            raise RuntimeError("context closed")

    assert session_only_hosts(DeadCtx()) == [], "a dead context must not break the session"

    assert roll_seed({SEED_MIN}) != SEED_MIN
    for bad in ("", "a/b", ".hidden", "a\\b"):
        try:
            identity_dir(bad, ".")
        except PreflightError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"identity {bad!r} should have raised")

    assert os.path.isfile(TEMPLATE), f"missing template {TEMPLATE}"
    template = Path(TEMPLATE).read_text(encoding="utf-8")
    assert "seed = 0" in template

    assert toml_value("en-GB") == '"en-GB"' and toml_value(0) == "0"
    assert toml_value(True) == "true" and toml_value(False) == "false"
    assert toml_value('a"b') == '"a\\"b"'

    # Substituting into the real template must keep every comment and still parse as TOML.
    import tomllib

    applied = apply_settings(
        template,
        {
            "seed": 12345,
            "proxy_server": "socks5://p:1080",
            "proxy_username": "u",
            "timezone": "Europe/London",
            "locale": "en-GB",
            "human_preset": "default",
            "storage_quota_mb": 0,
        },
    )
    parsed = tomllib.loads(applied)
    assert parsed["identity"]["seed"] == 12345
    assert parsed["identity"]["storage_quota_mb"] == 0
    assert parsed["network"]["proxy_server"] == "socks5://p:1080"
    assert parsed["network"]["timezone"] == "Europe/London"
    assert parsed["network"]["locale"] == "en-GB"
    assert parsed["browser"]["human_preset"] == "default"
    assert parsed["browser"]["headless"] is False, "defaults must survive a substitution"
    # The comments are the reason the template exists — they must all still be there.
    assert applied.count("#") == template.count("#")
    assert "ALWAYS false" in applied and "RECOMMENDED" in applied
    assert applied.count("\n") == template.count("\n"), "substitution must not add/drop lines"
    # A short value keeps its comment in the original column; a long one pushes it right
    # by exactly as much as it needs and no more.
    short = apply_settings(template, {"locale": "de"})
    orig_col = next(ln.index("#") for ln in template.splitlines() if ln.startswith("locale"))
    assert next(ln.index("#") for ln in short.splitlines() if ln.startswith("locale")) == orig_col
    long = apply_settings(template, {"locale": "x" * 200})
    assert next(ln for ln in long.splitlines() if ln.startswith("locale")).count("  #") == 0
    # And the result must still drive a launch.
    kwargs, _ = derive(parsed)
    assert "--fingerprint=12345" in kwargs["args"]

    # A key that is not in the template is a drift bug, not a silent no-op.
    try:
        apply_settings(template, {"nonexistent_key": 1})
    except PreflightError as exc:
        assert "drifted" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown key should have raised")

    def fake_args(**kw):
        base = {
            "proxy": None,
            "proxy_user": None,
            "timezone": None,
            "locale": None,
            "human_preset": None,
            "storage_quota_mb": None,
        }
        return argparse.Namespace(**{**base, **kw})

    assert settings_from_args(fake_args()) == {}, "no flags must mean pure defaults"
    assert settings_from_args(fake_args(proxy="http://h:1"))["proxy_server"] == "http://h:1"
    assert settings_from_args(fake_args(proxy=""))["proxy_server"] == "", "'' clears the proxy"
    for bad in (
        {"proxy": "h:1"},
        {"timezone": "London"},
        {"locale": "English"},
        {"storage_quota_mb": -1},
    ):
        try:
            settings_from_args(fake_args(**bad))
        except PreflightError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{bad} should have raised")

    print("self-test: ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--identity", help="identity name = directory under .stealth/")
    ap.add_argument("--root", default=".", help="project root holding .stealth/ (default: cwd)")
    ap.add_argument("--url", help="navigate here after launch")
    ap.add_argument("--init", action="store_true", help="scaffold the identity and roll a seed")
    # Settings applied at --init. Omit them all for pure defaults, which are already the
    # strongest anti-recognition settings. On an EXISTING identity these retune it in
    # place, keeping the seed and every comment.
    ap.add_argument(
        "--proxy",
        metavar="URL",
        help="network.proxy_server (http/https/socks5, CONNECT-only); '' clears it",
    )
    ap.add_argument(
        "--proxy-user",
        metavar="USER",
        help="network.proxy_username; password comes from STEALTH_PROXY_PASSWORD",
    )
    ap.add_argument("--timezone", metavar="TZ", help="network.timezone, e.g. Europe/London")
    ap.add_argument("--locale", metavar="TAG", help="network.locale, e.g. en-GB")
    ap.add_argument(
        "--human-preset",
        choices=["default", "careful"],
        help="browser.human_preset (default: careful, from the template)",
    )
    ap.add_argument(
        "--storage-quota-mb",
        type=int,
        metavar="MB",
        help="identity.storage_quota_mb; 0 leaves the binary's normalized value",
    )
    ap.add_argument("--check", action="store_true", help="preflight only, do not launch")
    ap.add_argument(
        "--print-args", action="store_true", help="print the resolved launch kwargs and exit"
    )
    ap.add_argument(
        "--no-canary",
        action="store_true",
        help="skip the pre-task canary even if profile.toml asks for it",
    )
    ap.add_argument(
        "--force", action="store_true", help="ignore a stale lock file (never a live one)"
    )
    ap.add_argument("--self-test", action="store_true", help="pure logic checks, no browser")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.identity:
        ap.error("--identity is required (or use --self-test)")

    try:
        if args.init:
            return init_identity(args.identity, args.root, settings_from_args(args))

        idir, cfg = preflight_config(args.identity, args.root)
        kwargs, notes = derive(cfg)

        if args.print_args:
            print(
                json.dumps(
                    {
                        "identity": args.identity,
                        "dir": idir,
                        "user_data_dir": os.path.join(idir, USER_DATA),
                        "launch_persistent_context": kwargs,
                        "notes": notes,
                    },
                    indent=2,
                    default=str,
                )
            )
            return 0
        if args.check:
            preflight_runtime(idir)
            for note in notes:
                print(f"NOTE: {note}", file=sys.stderr)
            print(f"STATUS identity: {idir}")
            print(f"STATUS seed: {cfg['identity']['seed']}")
            print(
                f"STATUS baseline: {'present' if os.path.exists(os.path.join(idir, BASELINE_NAME)) else 'not yet written'}"
            )
            print("RESULT: ok")
            return 0

        want_canary = bool((cfg.get("verify") or {}).get("canary_before_task", True))
        if want_canary and not args.no_canary:
            rc = run_canary(args.identity, args.root)
            if rc != 0:
                print(
                    "PREFLIGHT: canary failed — not handing over the session. Fix the "
                    "reported checks, or re-run with --no-canary to override deliberately.",
                    file=sys.stderr,
                )
                print("RESULT: fail")
                return 1

        return hold_open(args.identity, args.root, args.url, args.force)
    except PreflightError as exc:
        print(f"PREFLIGHT: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    except Exception as exc:
        print(f"LAUNCH-ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1


if __name__ == "__main__":
    sys.exit(main())
