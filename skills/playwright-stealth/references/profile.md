# Using an identity at launch

How an identity is laid out on disk, how the launcher guards it, and how to drive one from
code. Verified 2026-08-25 against `cloakbrowser` 0.5.8 wrapper source.

**What every `profile.toml` key means, the derivation rules that turn it into launch
arguments, identity hygiene, and the proxy ladder are not here** — they belong to the
`playwright-stealth-identity` skill, which is the only thing that writes that file. See its
`references/identity.md`. This file covers what happens once the identity already exists.

**Contents:** [Layout](#layout) · [Single-writer locking](#single-writer-locking) ·
[Driving from code](#driving-from-code) · [Authorized use](#authorized-use)

## Layout

```
<project>/
  .gitignore              <- must contain `.stealth/` before the first launch
  .stealth/
    README.md             <- "this directory holds live credentials"
    <identity>/           <- e.g. .stealth/acme-portal/
      profile.toml        <- settings; the identity skill writes it, this one only reads it
      user-data/          <- CloakBrowser user_data_dir: cookies, logins, localStorage
      baseline.json       <- canary fingerprint baseline, machine-written
      .lock               <- single-writer guard
```

Two rules about this tree:

- **Our files sit beside `user-data/`, never inside it.** That directory belongs to
  Chromium, which rewrites and prunes it; anything you leave in there is not yours.
- **`.stealth/` is gitignored before anything launches.** The identity skill appends the
  entry when it creates the identity, and every launch re-checks with `git check-ignore`
  and refuses if it is missing. Directories are `0700` and `profile.toml` `0600`.

## Single-writer locking

A Chromium profile is single-writer. Two processes on one `user-data/` corrupts it.

`launch.py` takes an advisory `flock` on `.lock` for the whole session. The kernel
releases it when the process dies, so a crash can never leave a stale lock behind — there
is no lock file to clean up and `--force` is not the normal path. Before that it also
reads Chromium's own `user-data/SingletonLock`, a symlink ending in the owning PID, and
refuses if that process is alive — which catches a browser someone started outside the
launcher. A dangling symlink from a crash is ignored.

Never delete `Singleton*` while a browser might still be running. If you need two tasks
at once, use two identities; that is the supported form of concurrency.

## Driving from code

Import the launcher's context manager rather than re-deriving the launch options. That is
what keeps the lock, the seed, the headful flag and the humanize settings in one place.
Note that it does **not** run the canary — `launch.py` does that around it, so run
`canary.py --identity <name>` yourself before a code-driven session you intend to trust:

```python
import os, sys
sys.path.insert(0, os.path.join(os.environ["CLAUDE_SKILL_DIR"], "scripts"))
from launch import open_identity

with open_identity("acme-portal") as ctx:      # root defaults to the cwd
    page = ctx.new_page()
    page.goto("https://example.com")
    page.click("#sign-in")                      # humanized: real cursor path + hold
    page.type("#email", "me@example.com", delay=60)
    page.mouse.wheel(0, 600)                    # real wheel input, never JS scrollTo
```

Three call-shape rules that decide whether humanization actually applies:

- Use `page.click(selector)`, `page.type(selector, text)`, `page.hover(selector)`, or any
  Locator — `page.get_by_role(...)`, `page.get_by_label(...)`, `page.locator(...)`. The
  whole `Locator` class is patched (`click`, `fill`, `type`, `hover`, `check`, `press`,
  `press_sequentially`, `select_option`, `drag_to`, `clear`,
  `scroll_into_view_if_needed` — verified in the wrapper source), so the durable
  role-based API is also the humanized one. **`page.query_selector()` returns an
  `ElementHandle` that bypasses every humanize patch** — the cursor teleports and
  keystrokes fire without timing.
- Use `time.sleep()`, not `page.wait_for_timeout()`. The Playwright call sends CDP
  protocol traffic that behavioural scoring picks up.
- Scroll with `page.mouse.wheel(...)`. JS `scrollTo`/`scrollBy` emits a `scroll` event
  with `isTrusted: false` and no underlying `wheel` — a binary, checkable bot tell, and
  one the canary tests for.

## Authorized use

Automate only properties you own or are authorized to test. Honor Terms of Service and
`robots.txt`. No fraud, credential abuse, mass account creation, denial-of-service, or
defeating access controls you have no right to bypass. Circumventing challenges,
authentication, or paywalls needs its own legal review. You are responsible for how you
use this.
