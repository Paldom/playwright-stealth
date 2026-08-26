# The canary — what it checks and what it cannot tell you

`canary.py` answers one question: **is this still the same un-tampered device I logged in
with?** It is drift detection against `baseline.json`, not proof of invisibility.

**Contents:** [How it runs](#how-it-runs) · [Required checks](#required-checks) ·
[Advisory checks](#advisory-checks) · [Failure to fix](#failure-to-fix) ·
[Re-baselining](#re-baselining) · [What it cannot tell you](#what-it-cannot-tell-you) ·
[External suites](#external-suites-advisory-only)

## How it runs

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <name>              # write or diff
python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <name> --json       # machine-readable
python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <name> --strict     # warnings fail too
python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <name> --rebaseline # accept a change
```

It opens the identity's own profile through the same locked path as `launch.py`, so it
never runs concurrently with a session — which means a run that starts with the automatic
canary opens two browser windows in sequence, the canary's and then yours. That is
deliberate: the canary launches with the identity's real settings (headful, seeded,
humanized) because a baseline measured under different settings would describe a
different device. Everything is measured on `about:blank`: the
canary **never navigates to a target site** and never touches a page you are working in.
The single property it installs (`window.__stealthWheel`, to read the wheel event's
`isTrusted`) is deleted before the page closes.

The first run writes `baseline.json` and reports that this run is the reference. Every
later run diffs against it. `RESULT: ok` (exit 0) means no required check failed.

`launch.py` runs the canary automatically before handing over a session when
`verify.canary_before_task = true`, and refuses on failure. `--no-canary` overrides that
deliberately.

## Required checks

A failure here exits 1. Each one is a real, checkable tell rather than a heuristic.

| Check | Why it is required |
| --- | --- |
| `navigator.webdriver` is `false` | The baseline automation signal. CloakBrowser patches it at the source level. |
| its getter still reads `[native code]` | A JS-patched getter is a *stronger* tell than the original value — `Function.prototype.toString` gives it away. If this fails, something is injecting JS stealth on top; remove it. |
| no `cdc_` / `__playwright` / `__pwInitScripts` / `_selenium` globals | Read with `Object.getOwnPropertyNames(window)`, not `Object.keys` — automation frameworks inject non-enumerable properties that `Object.keys` misses. |
| canvas hash identical across two draws | A real browser drawing the same scene twice returns an identical hash. A *jittering* hash means a per-call noise injector, which is itself abnormal for Chromium. |
| canvas hash identical to `baseline.json` | The live proof that `--fingerprint=<seed>` is holding. If this moves with no version change, the seed is not reaching the binary and every launch is a new device. |
| WebGL renderer is not SwiftShader/llvmpipe | A software rasterizer means no GPU-backed rendering, which on a headful Mac means something is badly wrong. |
| wheel input is `isTrusted` | Real driver input is trusted. JS `scrollTo`/`scrollBy` produces an untrusted `scroll` event with no `wheel` at all. |

If the wheel value cannot be measured at all, the canary reports that as a visible
advisory warning rather than silently dropping a required check — and `--strict` turns it
into a failure.

## Advisory checks

Reported, and only fatal under `--strict`. These move for legitimate reasons (an OS
update, a new monitor, a display-scale change), so they inform rather than gate.

- **Client Hints coherence** — `getHighEntropyValues()` platform agrees with
  `navigator.userAgent`. Note that `navigator.userAgentData` is **secure-context only**,
  and `about:blank` has an opaque origin, so on a local run the API is simply absent and
  the check reports that as informational rather than as a warning. It only warns if the
  API is present and throws.
- **User agent matches `navigator.platform`** — the coherence check that does work
  without a secure context, and the one that catches a Windows UA over a Mac platform.
- **Timezone matches the configured egress** — only judged when `network.timezone` is set.
  This catches a mis-edited `profile.toml`.
- **Browser timezone matches the real exit IP** — `--check-egress` only, because it is the
  one measurement that leaves the machine. It resolves the live exit IP and compares its
  timezone to the browser's. **Run it once whenever you attach a proxy.** `geoip = true`
  derives the timezone from a GeoIP database, and that database can be wrong: a real
  commodity proxy exit IP in London (GB) resolved the browser clock to
  `America/Chicago` — a US clock on a UK address, which is a louder tell than any default
  would have been. Nothing else in the canary can catch that, because with `geoip` doing
  the work there is no configured value to compare against. The fix is to set
  `network.timezone` and `network.locale` explicitly; they override geoip.
- **`window.chrome` present** and **plugin list non-empty** — stock automated Chromium
  gets both wrong; CloakBrowser should get both right.
- **`hardwareConcurrency` plausible.**
- **No other fingerprint drift** — user agent, platform, languages, device memory, screen
  metrics, WebGL strings, timezone, all compared to the baseline.
- **Persistent-cookie storage works** — from the second run onward, proof that the profile
  round-trips cookies to disk at all. If this fails, nothing else in the identity design
  matters. **It is not proof that you are still logged in anywhere**, and it must not be
  read that way: the marker carries an explicit one-year expiry, so it passes by
  construction. An earlier version of this check was labelled "marker cookie survived" and
  was taken as a durable-login signal, which it never was.
The canary deliberately does **not** check whether you are still logged in. It launches its
own browser, so session cookies from the previous run are already deleted before it can
look, and it never visits a target site. A row here would pass every time and read as "your
logins are durable" when it really meant "there is nothing left to see". `launch.py` prints
that warning instead, at the end of a live session, which is the only moment the evidence
still exists.

### Why a persistent profile still loses logins

`launch_persistent_context` writes every cookie the site sets, and persistent ones survive
exactly as promised. What it cannot do is make a **session cookie** persistent — that is
the site's choice, expressed as `is_persistent=0`, and Chromium honours it by deleting the
cookie on close.

Observed on real vendor dashboards, 2026-08-25: an affiliate platform's `IMPACT_MEMBER_SID`
and a proxy dashboard's `SNS` were both session-scoped, while everything that *did* persist
for those hosts was analytics and anti-fraud fingerprinting (`fingerprintComponents`,
`_sn_*`, `__stripe_*`, `nQ_*`). The profile was intact; the logins were gone.

Three things follow, in order of value:

1. **Tick "remember me" / "keep me signed in" at hand-login.** That is what makes the site
   issue a persistent token instead of a session one. It is the single highest-value habit
   in this whole toolkit, and it costs one click.
2. **If a site offers no such option, its login cannot outlive the browser.** Do the work
   inside one live session rather than expecting to resume — or drive it with a recorded
   job that logs in as its first step.
3. **Check before you rely on it.** Close the browser, reopen the identity, and load a
   logged-in-only page. The canary cannot do this for you: it never visits target sites, so
   it can see that cookies persist but not whether any given site still trusts you.

## Failure to fix

| Failing check | Likely cause | Fix |
| --- | --- | --- |
| `navigator.webdriver is false` | not launched through CloakBrowser | launch via `launch.py` / `open_identity()`; a plain Playwright Chromium will always fail this |
| `webdriver getter is native` | a JS stealth shim is layered on top | remove `playwright-stealth`-style patches; the engine already handles it |
| `no automation globals` | a driver or extension injected them | check what else attaches to the browser; nothing should |
| `canvas hash stable in-session` | a canvas-noise injector is active | remove stacked anti-fingerprint patches; the binary handles canvas |
| `canvas hash matches baseline`, no version change | the seed is not reaching the binary | `launch.py --identity <n> --print-args` and confirm `--fingerprint=<seed>` is in `args`; check `identity.seed` is non-zero |
| `canvas hash matches baseline`, after an upgrade | the binary or wrapper changed | expected — review, then `--rebaseline` |
| `WebGL renderer is hardware` | headless, or GPU unavailable | set `headless = false`; on Linux use a real or virtual display |
| `wheel input is trusted` | scrolling via JS | use `page.mouse.wheel(...)` |
| `persistent-cookie storage works` (advisory) | profile not persisting at all | confirm `user-data/` is writable and the session closed cleanly rather than being killed |
| `NOTE: … only session-scoped cookies` from `launch.py` | the site issued a session-only auth cookie | log in again and tick "remember me" / "keep me signed in"; if the site offers no such option, its login cannot outlive the browser and the flow must run inside one live session |
| `timezone matches egress` (advisory) | `profile.toml` disagrees with the proxy | re-run `python3 -m cloakbrowser info --proxy <url>` and set `timezone`/`locale` to what it reports |

Never disable, weaken, or edit the canary to make it pass, and never report a session as
verified from a `RESULT: fail`.

## Re-baselining

A browser or wrapper upgrade legitimately changes the fingerprint. When the canvas hash
moves *and* the recorded `wrapper`/`binary` version differs, the failure message says so
and names `--rebaseline`. That is a **reviewed** action: it means "I know why the device
changed and I accept it". A re-baseline never happens silently, and it never happens
while a required check is failing.

Note the consequence: to a site that fingerprinted your session, a re-baseline is a new
device. Expect re-verification on the next visit, and prefer to re-baseline at a moment
when that is cheap.

## What it cannot tell you

Passing the canary means there is no obvious local tell. It does not mean a live
commercial wall will let you through. It cannot exercise:

- **Network-layer scoring.** TLS/JA3/JA4 and HTTP/2 settings are negotiated before any
  JavaScript runs. Cloudflare pairs the JA4 hash with cohort statistics — request volume,
  cache-hit ratio, protocol mix — so matching a hash is only the first gate.
- **Session-long behavioural scoring.** Cloudflare's Precursor (announced July 2026)
  records pointer, keyboard, scroll, clipboard and page-visibility streams across a whole
  visit and can revoke clearance mid-session. One page load does not exercise that.
- **IP reputation**, which is scored before any of this.

The behavioural corollary worth internalising: Akamai reported in August 2026 that 63.2%
of tested agent requests carried **no mouse events at all**. The tell that catches agents
today is the *absence* of behaviour, not imperfect behaviour — which is why `humanize` and
realistic dwell time matter more than tuning any single motion constant. Every px/s and
millisecond figure in this space is a directional heuristic from non-peer-reviewed
sources; treat them as calibration knobs, never as pass/fail thresholds.

## External suites (advisory only)

Public bot-test pages are useful evidence for a human to look at. They are **never** the
release oracle: they change without notice, rate-limit automated traffic, and disagree
with each other. Only visit them where you are authorized to.

| Tool | URL | Reads as |
| --- | --- | --- |
| Sannysoft | `https://bot.sannysoft.com` | visual green/red table |
| BrowserScan | `https://www.browserscan.net/bot-detection` | visual verdict, includes the incognito/quota check |
| Brotector | `https://kaliiiiiiiiii.github.io/brotector/` | machine-readable JSON of detections |
| rebrowser bot-detector | `https://bot-detector.rebrowser.net/` | machine-readable `#detections-json`; covers `runtimeEnableLeak`, `sourceUrlLeak`, `mainWorldExecution` |
| bot.incolumitas | `https://bot.incolumitas.com/` | machine-readable behavioural score, needs interaction and time |
| deviceandbrowserinfo | `https://deviceandbrowserinfo.com/are_you_a_bot` | bot verdict plus an API |
| BrowserLeaks | `https://browserleaks.com` (`/canvas`, `/webgl`, `/tls`, `/webrtc`) | raw values including your JA3/JA4 — eyeball, not a verdict |

**CreepJS trust and bot scores now print 0.00 for every engine** — upstream disabled them
([creepjs#292](https://github.com/abrahamjuliot/creepjs/issues/292)). Any 2026 comparison
quoting a CreepJS trust score is quoting a dead column. If you use CreepJS at all, use the
official GitHub Pages deployment and read the lie list, not the score.

Commercial walls (Cloudflare, DataDome, Akamai, Kasada, HUMAN/PerimeterX) have no neutral
pass/fail page. The only signal is whether you reach content or a challenge, and you
should only measure that against a property you are authorized to test.
