---
name: playwright-stealth-setup
description: Installs and verifies the CloakBrowser stealth-Chromium toolchain — pip package, ~200 MB patched binary, licence key, macOS unquarantine. Use to install or get the stealth browsing environment ready, or when it is missing or broken — 'set up cloakbrowser', 'cloakbrowser install failed'. Not for installing plain Playwright or Chrome, creating an identity or profile, or launching a session.
license: MIT
---

# playwright-stealth-setup

Get the [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) stealth-browsing
toolchain installed and verified on this machine, idempotently: probe what is present,
then install only what is missing. CloakBrowser is a Chromium fork with fingerprint
patches applied at the C++ source level, driven through Playwright — you install a pip
wrapper, then download its patched browser binary separately.

**Authorized use only.** This toolchain is for automating properties you own or are
authorized to test, and for lawful, privacy-respecting browsing. Respect Terms of
Service and `robots.txt`; no fraud, credential abuse, mass account creation, or evading
access controls you have no right to bypass. Full policy in
[references/toolchain.md](references/toolchain.md).

## When to use / when NOT to use

Use when the environment is missing or broken: `cloakbrowser` not importable, the binary
not downloaded, a failing launch test, macOS refusing to open the app, a licence key to
add, or "get the stealth env ready". **Not** for running a browsing task, creating an
identity, or resuming a session — that is the `playwright-stealth` skill, which installs
nothing.

## Workflow

Run everything through the bundled script. It is the deterministic gate.

1. **Probe first — no changes.** See what already exists before touching anything:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py"
   ```
   Read the `STATUS <component>: OK|MISSING|WARN|FAIL|SKIP` lines and the final
   `RESULT:`. `python`, `cloakbrowser_pkg`, `binary` and `launch` are **required**;
   `geoip_extra` is advisory and never fails the run. `--json` gives machine-readable
   output. `--quick` gives a fast, fully offline component list but **can never return
   `RESULT: ok`** — it skips the launch test, and an unexecuted binary is not a verified
   install. Use the plain form for the gate.

2. **Install what is missing.** Only if step 1 reported `RESULT: fail`:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py" --install
   ```
   This runs `pip install -U 'cloakbrowser[geoip]'`, then `python -m cloakbrowser
   install` (~200 MB on first run), then clears the macOS Gatekeeper quarantine, then
   re-probes. It skips anything already `OK`.

3. **Add a licence key if the user wants a newer binary** (optional — the toolchain
   works without one):
   ```bash
   python3 -m cloakbrowser login          # GitHub sign-in for a free key, or paste a paid key
   ```
   Or export `CLOAKBROWSER_LICENSE_KEY` in the shell. **Never** write the key into a
   file in the repository, and never pass it as a visible command-line argument.
   Re-run step 1 afterwards — the binary version and `license.tier` should change.

4. **Verify and report.** Re-run step 1 and require `RESULT: ok`. Report to the user:
   the wrapper version, the **Chromium version that will actually launch**, and the
   **licence tier** — all read from the live diagnostic, never from documentation.
   **Do not launch a browsing session or create an identity here.**

If a required step fails, read the `FAIL`/`MISSING` detail and fix the root cause using
the troubleshooting table in [references/toolchain.md](references/toolchain.md). Never
edit the script to get past the gate, and never report success from a `RESULT: fail`.

## What "done" looks like

- `setup_check.py` prints `STATUS binary: OK chromium <version> (licence tier: <tier>)`,
  `STATUS launch: OK`, and `RESULT: ok` (exit 0).
- You reported the resolved Chromium version and licence tier, and named the trust
  boundary below in one or two sentences.
- No browser session was started and no `.stealth/` identity was created.

## Gotchas

- **`cloakbrowser info` exits 0 even when nothing is installed.** `binary.installed` can
  be `false` on a successful exit. The script reads the JSON, not the exit code — so
  should you if you ever probe by hand.
- **The binary is licence-gated, the wrapper is not.** MIT covers the Python wrapper
  only. Without a key you get the pinned public build for your platform, which on macOS
  trails current Chrome by several major versions. `cloakbrowser login` (free, GitHub
  sign-in) moves you to the latest Stable build.
- **The Chromium sandbox is disabled.** `--no-sandbox` is part of the wrapper's default
  stealth args and cannot be removed without `stealth_args=False`, which would turn off
  every fingerprint patch. Combined with an old free-tier binary, a renderer exploit
  lands on the host. Say this to the user; do not bury it.
- **macOS quarantines the binary.** It is ad-hoc signed, so the first launch fails with
  "app is damaged". `--fix-quarantine` clears it with `xattr -cr` scoped to
  `~/.cloakbrowser/`, and only after `cloakbrowser install` has already verified the
  download against a pinned Ed25519 signature — the Gatekeeper flag is cleared on a file
  whose publisher is authenticated, not on an arbitrary download. This is also the usual
  cause of a `launch: FAIL` on a fresh Mac.
- **You do not need `playwright install chromium`.** CloakBrowser downloads its own
  binary. On Linux you may still need `playwright install-deps chromium`.
- **Python ≥ 3.11 is required** because the identity format is read with stdlib
  `tomllib`. Fix the interpreter; do not add a TOML backport.
- **Vendor benchmark claims are marketing.** "Passes every bot detection test" and
  "0.9 reCAPTCHA v3" are unreproduced. Report what the diagnostic says, nothing more.
- **Binaries drift.** After any wrapper or binary upgrade, the `playwright-stealth`
  canary will report fingerprint drift against its baseline — that is correct behaviour
  and needs a reviewed re-baseline, not a suppressed check.

## References

- [references/toolchain.md](references/toolchain.md) — install matrix, the
  `cloakbrowser info` JSON contract, licence tiers, macOS Gatekeeper, the full trust
  boundary, version pinning and rollback, troubleshooting, cleanup, authorized use.
