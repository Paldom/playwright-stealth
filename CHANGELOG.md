# Changelog

All notable changes to this repository's skills are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org) on the plugin manifest
(breaking skill-interface change → major, new skill → minor, fix → patch).

## [Unreleased]

## [0.4.0] - 2026-09-22

### Added
- **Failure diagnosis for recorded jobs.** A failed replay now captures `failure.json`
  (title, URL, HTTP status of the page it shows, visible text, password-field count,
  visible iframe sources) and prints a `DIAGNOSE:` command. `pagestate.py --run <dir>
  --offline` labels the page from signatures alone; without `--offline`, and only when
  `TYPESAFE_API_KEY` is set and the user agreed, one TypeSafe Jev request over the
  redacted title and text labels the rest: a suspected dead session, consent overlay,
  bot challenge or block, site error, or content with no wall. Every label is a triage
  hint, not a verdict; replay itself still makes no model call. Scope chosen after an
  `/advise-max` consultation (Fable 5.1 and GPT-6 Astra answered; Gemini, Grok and Kimi
  failed on quota or setup): both rejected Jev as a navigation loop for one-off browsing
  and as anything inside replay, and accepted it only as an opt-in, advisory,
  post-failure label. A Codex review of the diff through `acpx` then found 13 issues —
  unredacted titles and links, a redirect that would carry the key, an agent-run send,
  invisible reCAPTCHA frames read as walls, a stale status — all fixed. Reasoning in
  [docs/jev-rationale.md](docs/jev-rationale.md).
- `manifest.json` records `last_status`, the HTTP status of the page showing when the run
  ended, or null when that page had no document response (for example `about:blank`).

### Changed
- `playwright-stealth` gotchas: the geoip/exit-IP paragraph was stated twice; the bullet
  now points at the "Verifying a proxy" section instead of repeating it.

## [0.3.0] - 2026-08-31

### Added
- Adopted the current skillskit gate: executed trigger evals scoring every trigger
  prompt against every skill description (rank-1 routing accuracy 84.1%), a security
  scan over skill content and bundled scripts, ruff lint and format, README-shape
  validation, pre-commit hooks and a write-time lint hook.

### Changed
- Skill descriptions sharpened where the eval gate showed a sibling outranking a
  skill on its own trigger prompts, or a stated non-trigger matching better than any
  trigger. Fixes changed the scope boundary, not just the wording.

### Fixed
- Findings the new lint gate surfaced in this repo's own scripts, fixed at the
  source; where a rule was wrong for a line it is suppressed there with its reason.


## [0.2.0] - 2026-08-26

### Fixed
- **The canary's durable-login signal was false assurance.** Its "marker cookie survived
  the last run" check set its own cookie with an explicit one-year expiry, so it passed by
  construction and was read as proof of a live login. It only ever proved the cookie file
  round-trips, and a profile whose logins had all expired still reported healthy. Renamed
  to "persistent-cookie storage works", with the limit stated in the check's own output.
- **Added the durability warning that was actually missing.** `launch.py` now reports, at
  the end of a live session, any host holding only session-scoped cookies (`expires == -1`)
  — those are signed out the moment the browser closes, however well the profile persists.
  It runs there rather than in the canary because the canary launches its own browser, by
  which point Chromium has already deleted the evidence. Observed in the wild on vendor
  dashboards: the auth cookies were session-scoped while everything that persisted was
  analytics and anti-fraud fingerprinting. The hand-login step now tells the user to tick
  "remember me", which is what makes a site issue a persistent token instead.

### Changed
- **Identity provisioning moved out of `playwright-stealth` into a new
  `playwright-stealth-identity` skill.** The parent's description no longer advertises
  "create a stealth identity" and its body hands off instead of creating; `launch.py`'s
  "no identity" error now names the skill that does. Split chosen by the owner after an
  `/advise-max` consultation in which 3 of 4 flagship models argued against it. The dissent
  and the mitigations are recorded in [docs/skill-split-rationale.md](docs/skill-split-rationale.md);
  the first-browse regression they predicted is mitigated by the hand-off error message
  rather than by auto-creation.
- `references/proxies.md` moved to the identity skill; `references/profile.md` slimmed to
  layout, locking and driving-from-code, with settings and derivation now authoritative in
  the identity skill's `references/identity.md`.
- `launch.py --init` gained typed settings flags (`--proxy`, `--proxy-user`, `--timezone`,
  `--locale`, `--human-preset`, `--storage-quota-mb`) that substitute into the commented
  template atomically, preserving comments and their column alignment, and retune an
  existing identity in place without touching its seed.

### Added
- `playwright-stealth-job` — records a browsing task as a committed Python job at
  `jobs/<identity>/<name>.py` and replays it with no model in the loop. `job.py` owns
  `--new`, `--lint`, `--run`, `--list`. Design settled by an `/advise-max` consultation
  that split 2–2 between a declarative step schema and sealed Python; resolved for Python
  by a verified fact both camps guessed at — CloakBrowser patches the whole `Locator`
  class, so the durable role-based API is also the humanized one. The safety a schema
  would give by construction comes from an AST lint instead: `wait_for_timeout`,
  `query_selector`, JS `scrollTo`/`scrollBy` and credential literals are hard errors, and
  the linter gates every run. `h.pause()` jitters from a per-run seed recorded in the
  manifest, so production replays vary while `--seed` reproduces any past run exactly.
  Failed runs keep a screenshot, DOM and trace; successful ones shed the trace and prune.
- `playwright-stealth-identity` — creates or retunes one identity per account. Interview
  with a defaults-first skip rule: zero questions when the user says defaults, names no
  proxy or geography, or the run is non-interactive. Never volunteers a proxy, because the
  host's own residential IP is the strongest option and offering the choice talks people
  into a worse one.
- `playwright-stealth-setup` — idempotent install and verification of the CloakBrowser
  stealth-Chromium toolchain. Gates on `python -m cloakbrowser info --json` (which exits
  0 even when nothing is installed, so the script reads the JSON), handles the licence
  key, clears the macOS Gatekeeper quarantine, and reports the Chromium version and
  licence tier that will actually launch instead of quoting documentation.
- `playwright-stealth` — create, resume and drive a pinned stealth identity. Each
  identity lives in a gitignored `.stealth/<name>/` holding `profile.toml`, `user-data/`
  and `baseline.json`. `launch.py` owns `--init` (seed roll with collision check,
  `.gitignore` guard), the single-writer lock, and the `profile.toml` derivation;
  `canary.py` writes a fingerprint baseline on the first run and diffs against it after,
  with `--rebaseline` for a reviewed browser upgrade.
- `docs/setup-prompt.md` — paste-ready goal prompt that orders both skills with a
  verifier gate on each.
- Repository scaffolded from the skills template.
