#!/usr/bin/env python3
"""Create or retune one CloakBrowser stealth identity. Opens no browser.

An identity is a directory in the consuming project, all of it gitignored:

    <project>/.stealth/<identity>/
        profile.toml    settings (hand-editable, read with stdlib tomllib)
        user-data/      CloakBrowser user_data_dir — cookies, saved logins, localStorage
        baseline.json   canary fingerprint baseline (written by the browsing skill)
        .lock           single-writer guard

Modes:
  --init          create the identity, or retune an existing one in place
  --print-args    show the launch kwargs this profile resolves to, then exit
  --self-test     pure logic checks, no browser, no network

Settings are flags, never hand-edits: they substitute values into the commented template
atomically and keep every comment. Omit them all for pure defaults, which are already the
strongest anti-recognition settings.

    identity.py --identity acme-portal --init
    identity.py --identity eu-shop --init --proxy socks5://h:1080 --timezone Europe/London

The proxy password comes from STEALTH_PROXY_PASSWORD in the environment — never this file,
never a command line where `ps` can read it.

Exit codes: 0 ok, 1 failure, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# One implementation, not two. The provisioning logic lives in the browsing skill's
# launch.py because that is what reads profile.toml at launch time; duplicating it here
# would let the two drift and silently disagree about what a profile means.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIBLING = os.path.join(os.path.dirname(_HERE), "playwright-stealth", "scripts")


def _load_core():
    if not os.path.isfile(os.path.join(_SIBLING, "launch.py")):
        raise SystemExit(
            "SETUP-ERROR: this skill needs the 'playwright-stealth' skill installed "
            f"alongside it (looked for {_SIBLING}/launch.py). They ship together — "
            "install both, or reinstall the plugin."
        )
    sys.path.insert(0, _SIBLING)
    import launch

    return launch


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--identity", help="identity name = directory under .stealth/")
    ap.add_argument("--root", default=".", help="project root holding .stealth/ (default: cwd)")
    ap.add_argument("--init", action="store_true", help="create the identity, or retune it")
    ap.add_argument(
        "--print-args", action="store_true", help="print the resolved launch kwargs and exit"
    )
    ap.add_argument("--self-test", action="store_true", help="pure logic checks, no browser")
    # Settings. On an existing identity these retune it in place, keeping the seed.
    ap.add_argument(
        "--proxy",
        metavar="URL",
        help="network.proxy_server (http/https/socks5, CONNECT-only); '' clears it",
    )
    ap.add_argument(
        "--proxy-user",
        metavar="USER",
        help="network.proxy_username; password from STEALTH_PROXY_PASSWORD",
    )
    ap.add_argument("--timezone", metavar="TZ", help="network.timezone, e.g. Europe/London")
    ap.add_argument("--locale", metavar="TAG", help="network.locale, e.g. en-GB")
    ap.add_argument(
        "--human-preset",
        choices=["default", "careful"],
        help="browser.human_preset (template default: careful)",
    )
    ap.add_argument(
        "--storage-quota-mb",
        type=int,
        metavar="MB",
        help="identity.storage_quota_mb; 0 leaves the binary's normalized value",
    )
    args = ap.parse_args()

    core = _load_core()

    if args.self_test:
        # The core owns the logic and its own tests; this asserts the seam between us.
        for name in (
            "init_identity",
            "settings_from_args",
            "preflight_config",
            "derive",
            "apply_settings",
            "PreflightError",
        ):
            assert hasattr(core, name), f"launch.py is missing {name} — the skills have drifted"
        assert core.settings_from_args(args) == {}, "no settings flags must mean pure defaults"
        probe = argparse.Namespace(**{**vars(args), "timezone": "Europe/London"})
        assert core.settings_from_args(probe)["timezone"] == "Europe/London"
        print("self-test: ok")
        return 0

    if not args.identity:
        ap.error("--identity is required (or use --self-test)")

    try:
        if args.init:
            return core.init_identity(args.identity, args.root, core.settings_from_args(args))

        idir, cfg = core.preflight_config(args.identity, args.root)
        kwargs, notes = core.derive(cfg)
        if args.print_args:
            print(
                json.dumps(
                    {
                        "identity": args.identity,
                        "dir": idir,
                        "launch_persistent_context": kwargs,
                        "notes": notes,
                    },
                    indent=2,
                    default=str,
                )
            )
            return 0
        ap.error("nothing to do — pass --init or --print-args")
    except core.PreflightError as exc:
        print(f"PREFLIGHT: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    except Exception as exc:
        print(f"IDENTITY-ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
