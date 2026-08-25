# CloakBrowser toolchain reference

Facts verified 2026-08-25 against PyPI `cloakbrowser` 0.5.8, the installed wrapper
source, and a live `python -m cloakbrowser info --json` run on macOS arm64. Anything
version-specific rots — the setup script reads it live rather than trusting this file.

**Contents:** [What gets installed](#what-gets-installed) ·
[The gate: cloakbrowser info](#the-gate-cloakbrowser-info) ·
[Licence tiers](#licence-tiers-and-what-actually-launches) ·
[macOS specifics](#macos-specifics) ·
[Trust boundary](#trust-boundary-say-this-out-loud) ·
[Pinning and rollback](#pinning-and-rollback) ·
[Troubleshooting](#troubleshooting) · [Cleanup](#cleanup) ·
[Authorized use](#authorized-use)

## What gets installed

| Piece | How | Notes |
| --- | --- | --- |
| `cloakbrowser` wrapper | `pip install -U 'cloakbrowser[geoip]'` | MIT. Pulls `playwright`, `httpx`, `cryptography`; `[geoip]` adds `geoip2` + `socksio`. |
| Patched Chromium binary | `python -m cloakbrowser install` (or the first launch) | ~200 MB, cached under `~/.cloakbrowser/`. Not on PyPI — downloaded from the vendor. |
| Licence key (optional) | `python -m cloakbrowser login`, or `CLOAKBROWSER_LICENSE_KEY` | Unlocks a newer binary. Never write it into a file in the repo. |

You do **not** need `playwright install chromium` — CloakBrowser ships its own binary.
On Linux you may still need Playwright's system libraries: `playwright install-deps chromium`.

Python must be **≥ 3.11**: the identity format (`profile.toml`) is read with the
standard-library `tomllib`. Do not install a third-party TOML backport to dodge this —
fix the interpreter.

## The gate: `cloakbrowser info`

`python -m cloakbrowser info --json` is the only honest probe. It reports the binary
that will *actually* launch for this platform and licence, which is not necessarily the
newest one that exists.

```
environment { wrapper, python, os, arch, platform_tag }
binary      { version, tier, path, installed, cache_dir, bundled_version, pinned, ... }
launch      { tested, ok, version, error | reason }
license     { tier }
geoip       { db_present, path, resolved? }
modules     { playwright, geoip2, aiohttp, websockets }
```

Three things to know before you build anything on it:

- **It exits 0 even when nothing is installed.** `binary.installed` can be `false` and
  the process still returns 0. Read the JSON, never the exit code.
- **`--quick` skips the launch test** and keeps the command fully network-free. Useful
  for a fast probe; it is not evidence that the binary works.
- **`--proxy <url>` resolves the exit IP, timezone and locale a launch would apply**
  through that proxy. This is the correct proxy preflight — run it before binding a
  proxy to an identity.
- **There is no patch count in the output.** Report the Chromium version and licence
  tier; the per-tier patch count only exists in the vendor's README table and drifts.

## Licence tiers and what actually launches

The wrapper is MIT. **The binary is not** — it is separately licensed and gated.

- **No key** → the pinned public build for your platform. On macOS arm64 that resolved
  to Chromium `145.0.7632.109.2` on 2026-08-25, while the wrapper's bundled reference
  version was `146.0.7680.177.5`.
- **Free key** (GitHub sign-in via `cloakbrowser login`) → the latest Stable build, one
  concurrent session.
- **Pro key** → the latest build plus more concurrent sessions.

The vendor's own README contradicts itself about which build the free tier gets, which
is exactly why the setup gate prints `binary.version` and `license.tier` from the live
diagnostic instead of quoting a table. **On the free tier the macOS build trails current
Chrome by several major versions** — that is a security fact as well as a stealth one
(see below), and it is worth saying to the user rather than burying.

## macOS specifics

- **Gatekeeper.** The downloaded binary is ad-hoc signed, so macOS quarantines it and
  the first launch fails with "app is damaged" or a Gatekeeper prompt. Clear it with
  `xattr -cr ~/.cloakbrowser/chromium-*/Chromium.app` — `setup_check.py --fix-quarantine`
  does exactly that, scoped to your own cache directory and nothing else.
- **Native platform.** On macOS the wrapper passes `--fingerprint-platform=macos` and
  runs as a native Mac browser. It only spoofs Windows on Linux. Leave this alone:
  a Windows claim over Mac font metrics is a seam, not a disguise.
- **Widevine / DRM does not work here.** The CDM seeding path is Linux-only. Do not
  build a check around DRM playback on macOS.

## Trust boundary — say this out loud

This is a third party's closed-source Chromium binary, and the profile it drives will
hold real logins. State all four of these to the user; none of them is a reason not to
proceed, and all of them are things they should know.

1. **The binary is unaudited.** Downloads are verified against a pinned Ed25519
   signature on the published checksums before extraction, and releases carry GPG tags
   plus Sigstore attestations — so a compromised mirror cannot swap the file. That
   authenticates the publisher; it does not audit the code.
2. **The Chromium sandbox is off.** `--no-sandbox` is part of the wrapper's default
   stealth args and cannot be removed without `stealth_args=False`, which would disable
   every fingerprint flag and defeat the point. A renderer exploit therefore lands
   directly on the host.
3. **The free build is old.** An outdated Chromium carries known unpatched
   vulnerabilities. Combined with (2), browsing hostile pages on the free tier is a real
   risk, not a theoretical one.
4. **Blast radius.** `user-data/` holds decryptable session cookies and saved
   passwords. Treat the whole identity directory as credential material.

Practical mitigations, in order of value: use a dedicated identity per account and never
the everyday browser profile; keep a licence key current so the binary is not years old;
prefer a separate OS user or machine for high-value accounts; never point this at a
profile holding accounts you cannot afford to lose.

Every vendor pass-rate claim ("passes every bot detection test", "0.9 reCAPTCHA v3") is
unreproduced marketing. Do not repeat it as fact.

## Pinning and rollback

| Goal | How |
| --- | --- |
| Reproducible binary | `CLOAKBROWSER_VERSION=146.0.7680.177.5` (env), or `browser_version=` per launch |
| Opt into Preview builds | `CLOAKBROWSER_RELEASE_CHANNEL=preview` (macOS falls back to Stable) |
| Skip download, use a local build | `CLOAKBROWSER_BINARY_PATH=/path/to/chrome` |
| Cap GeoIP resolution time | `CLOAKBROWSER_GEOIP_TIMEOUT_SECONDS` (default 5) |
| Roll back the wrapper | `pip install cloakbrowser==<older>` — each wrapper release hardcodes a binary version |

A version pin overrides the release channel. Pins are never sticky: an unpinned launch
goes back to the latest available build. A browser upgrade legitimately changes the
fingerprint — expect the identity canary to demand a reviewed re-baseline afterwards.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `STATUS cloakbrowser_pkg: MISSING` | package absent | `setup_check.py --install` |
| `STATUS binary: MISSING` while `info` exits 0 | binary never downloaded | `setup_check.py --install` (≈200 MB) |
| `STATUS launch: FAIL` on macOS, "app is damaged" | Gatekeeper quarantine | `setup_check.py --fix-quarantine` |
| `STATUS launch: FAIL` on Linux, missing libs | Playwright system deps absent | `playwright install-deps chromium` |
| `STATUS geoip_extra: WARN` | installed without the extra | `pip install -U 'cloakbrowser[geoip]'` |
| `STATUS python: FAIL` | interpreter older than 3.11 | run under a 3.11+ interpreter; no TOML backport |
| Download stalls or 403s | network, proxy, or a stale mirror | retry; check `CLOAKBROWSER_DOWNLOAD_URL` is unset |
| `license.tier` still `free` after adding a key | key not visible to this process | `cloakbrowser login`, or export `CLOAKBROWSER_LICENSE_KEY` in the same shell |

Never edit the skill's scripts to get past the gate, and never report success from a
`RESULT: fail`. Fix the root cause.

## Cleanup

```bash
python -m cloakbrowser clear-cache   # remove cached binaries (frees ~200 MB per version)
python -m cloakbrowser logout        # remove the saved licence key
pip uninstall cloakbrowser
```

Identity profiles live in the consuming project under `.stealth/` and are *not* removed
by any of these — delete those directories yourself, and remember they contain live
session credentials.

## Authorized use

This toolchain is for automating properties you own or are authorized to test, and for
lawful, privacy-respecting browsing. Honor Terms of Service and `robots.txt`. Do not use
it for fraud, credential abuse, mass account creation, denial-of-service, or to defeat
access controls you have no right to bypass. Circumventing challenges, authentication,
or paywalls needs its own legal review — it is not a skill-design decision. You are
responsible for how you use it.
