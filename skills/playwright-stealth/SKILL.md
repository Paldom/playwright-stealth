---
name: playwright-stealth
description: Browses, logs in, and automates sites in a headful, humanized CloakBrowser stealth session on an existing pinned identity, then self-verifies against a baseline. Use when the user wants to browse or automate a site stealthily or 'like a human', resume a saved session, or run the canary. Not for installing the toolchain, creating an identity, replaying a saved job, or writing Playwright tests.
license: MIT
---

# playwright-stealth

The entry point: run a task in a [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)
stealth session — **headful**, **humanized**, on a **pinned identity** — then verify the
session has not drifted. CloakBrowser is a Chromium fork with fingerprint patches applied
at the C++ source level, driven through the ordinary Playwright API.

Two properties make a hand-made login reusable by an agent weeks later, and this skill
exists to hold both:

- **The profile keeps the session.** `user-data/` carries cookies, saved logins and
  localStorage across restarts.
- **The seed keeps the device.** With no `--fingerprint=<seed>` the binary rolls a fresh
  random fingerprint *every launch*, so a site that fingerprints your session sees a new
  device each time and re-verifies. The seed is mandatory here, not optional.

**Resuming is just relaunching** with the same `user-data/` and the same seed. There is
no export, import, or `storage_state` step.

**Authorized use only.** Automate only properties you own or are authorized to test;
respect Terms of Service and `robots.txt`; no fraud, credential abuse, mass account
creation, or defeating access controls you have no right to bypass. Full policy in
[references/profile.md](references/profile.md).

## When to use / when NOT to use

Use for any "do something in a stealth or human-like browser" task — open a page, log in,
fill a form, click through a flow, resume a session, or check that a profile is not leaking
automation tells.

**Not** for installing or repairing the toolchain (`playwright-stealth-setup`), and **not**
for creating or reconfiguring an identity — that is `playwright-stealth-identity`, which
owns `profile.toml`. This skill installs nothing, creates nothing, and fails fast if either
the toolchain or the identity is missing.

**If the task will happen more than once, hand off to `playwright-stealth-job`** once you
have driven it successfully here. It records the flow as a committed Python job that
replays with no model in the loop, which is cheaper, faster and auditable. Driving it from
this skill every time is the right choice only for one-offs.

## Workflow

1. **Confirm the toolchain, then the identity.** Both in one step:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/launch.py" --identity acme-portal --check
   ```
   `PREFLIGHT: cloakbrowser is not importable` means run the **playwright-stealth-setup**
   skill first, then retry. `no identity at …` means go to step 2.

2. **No identity yet? Hand off, do not improvise.** `no identity at …` means the
   **playwright-stealth-identity** skill has not run for this name. Load it, let it create
   the identity, then come back to step 3. Do not create one here and do not hand-edit
   `profile.toml` — that skill owns it, and settings applied by hand skip its validation.

   The same applies mid-task: if the user wants a proxy bound, a timezone corrected, or a
   second identity, that is the identity skill's job, not a detour in this one.

3. **Log in by hand, once.** Open the browser and hand the window to the user. Do not
   type their credentials yourself:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/launch.py" --identity acme-portal --url https://example.com/login
   ```
   They log in in the window and press Enter in the terminal, which closes the browser and
   flushes the profile to disk. Cookies now live in `user-data/`; the device is the seed.

   **Tell them to tick "remember me" / "keep me signed in".** This is not optional advice.
   Without it most sites issue their auth token as a *session* cookie, which Chromium
   deletes on close no matter how well the profile persists — the profile survives and the
   login does not. It is one click and it is the difference between a session you can
   resume for weeks and one that dies when the window shuts.

   Then prove it, before anyone depends on it: close the browser, relaunch the same
   identity, and load a page that only renders when signed in. A profile that "looks
   saved" is not evidence.

4. **Verify, before you trust the session.**
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity acme-portal
   ```
   The first run writes `baseline.json`; later runs diff against it. `RESULT: ok`
   (exit 0) means no required check failed. On any `[FAIL]`, use the failure-to-fix table
   in [references/canary.md](references/canary.md) — never disable a check to get green.

   `launch.py` already runs this for you before handing over a session
   (`verify.canary_before_task = true`) and refuses on failure — which is why you may see
   a second, short-lived browser window. That is deliberate: the canary measures the
   identity's *real* headful settings, or its baseline would describe a different device.
   **`open_identity()` in step 5 does not run it** — call the command above first.

5. **Drive the task from code.** Import the launcher's context manager — it owns the
   lock, the seed, and the headful/humanize settings, so nothing has to be re-derived:
   ```python
   import os, sys, time
   sys.path.insert(0, os.path.join(os.environ["CLAUDE_SKILL_DIR"], "scripts"))
   from launch import open_identity

   with open_identity("acme-portal") as ctx:       # root defaults to the cwd
       page = ctx.new_page()
       page.goto("https://example.com/account")
       page.click("#invoices")                      # humanized cursor path + hold
       page.mouse.wheel(0, 600)                     # real wheel input
       time.sleep(2)                                # NOT page.wait_for_timeout
       print(page.title())
   ```
   Or just hold the browser open for hands-on use by re-running the step-3 command with
   the same identity — that *is* the resume, and it canaries first.

## Verifying a proxy someone else bound

Binding a proxy belongs to **playwright-stealth-identity**. Verifying that it actually
behaves is this skill's job, and it is the step people skip:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <name> --check-egress
```

That compares the browser's clock against the live exit IP. Run it once after any proxy
change. It exists because `geoip = true` derives the timezone from a GeoIP database and
those databases go stale — a real London exit IP was observed setting the browser to
`America/Chicago`, which is a louder tell than no proxy at all. If it disagrees, the fix is
`timezone`/`locale` set explicitly, which means going back to the identity skill.

## What "done" looks like

- The task ran in the browser, and the same identity relaunched later loads the persisted
  cookies and logins.
- `canary.py` reported `RESULT: ok` before you relied on the session against the target.
- You told the user which identity was used, that `.stealth/` holds live credentials, and
  anything the launcher reported as a `NOTE:` (a weakened setting, a missing proxy
  password, a normalized storage quota).

## Gotchas

- **An unseeded launch is a new device.** `seed = 0` is a hard error, and the fix is
  the identity skill — never a direct `launch_persistent_context()` call that skips it. If
  you ever see the canary's "canvas hash matches baseline" fail with no version change,
  the seed is not reaching the binary.
- **Single-writer profile.** Never open one `user-data/` from two processes; it corrupts
  the profile. The launcher holds an advisory `flock` for the whole session and also
  refuses if Chromium's own `SingletonLock` names a live PID. To run two tasks at once,
  use two identities. Never delete `Singleton*` while a browser might be running.
- **`page.query_selector()` bypasses humanize entirely.** `ElementHandle` objects skip
  every patch: the cursor teleports, keystrokes fire without timing, scroll has no curve.
  Use `page.click(selector)`, `page.type(selector, text)`, `page.hover(selector)` or
  `page.locator(selector).*`.
- **`page.wait_for_timeout()` is a tell.** It sends CDP protocol traffic that behavioural
  scoring picks up. Use `time.sleep()`.
- **Never JS `scrollTo`/`scrollBy`.** They emit a `scroll` event with `isTrusted: false`
  and no underlying `wheel` — a binary, checkable bot tell. Use `page.mouse.wheel(...)`.
- **Coherence beats disguise.** Leave `identity.platform` empty: on a Mac the binary
  presents as macOS and that is correct. A Windows claim over Mac font metrics is a seam.
- **`geoip = true` is not self-verifying.** It reads a GeoIP database, and the database
  can be wrong — a real London exit IP has been observed setting the browser clock to
  `America/Chicago`. After attaching any proxy, run `canary.py --check-egress` once: it
  resolves the live exit IP and compares its timezone to the browser's. If they disagree,
  set `network.timezone` and `network.locale` explicitly, which override geoip.
- **The profile is credential material.** `user-data/` holds decryptable session cookies
  and saved passwords. Gitignored, `0700`, never copied between machines, never shared.
- **The Chromium sandbox is off.** `--no-sandbox` is part of the wrapper's default stealth
  args and cannot be removed without disabling every fingerprint patch. Do not point this
  at accounts you cannot afford to lose, and keep the binary current.
- **A browser upgrade changes the fingerprint.** That is real drift and the canary is
  right to fail. Review it, then `canary.py --rebaseline`. To the target site a
  re-baseline is a new device, so expect one round of re-verification.
- **Passing the canary is not passing a live wall.** It proves there is no obvious local
  tell. It cannot exercise TLS/JA4 cohort scoring, IP reputation, or session-long
  behavioural monitoring.

## References

- [references/profile.md](references/profile.md) — storage layout, single-writer locking,
  driving from code, and the call shapes that decide whether humanization applies.
- [references/canary.md](references/canary.md) — required vs advisory checks, the
  failure-to-fix table, re-baselining, and what the canary cannot tell you.

What every `profile.toml` key means, the derivation rules, and the proxy ladder live with
the skill that owns them — **playwright-stealth-identity**.
