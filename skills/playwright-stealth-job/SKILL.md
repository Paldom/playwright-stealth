---
name: playwright-stealth-job
description: Records a browsing task as a committed Python job on a stealth identity, then replays it with no model in the loop — lint-gated, seed-jittered, with a run manifest. Use to make a browser flow repeatable or scheduled, replay or list the saved jobs, cut per-run LLM cost, or diagnose a failed run and repair the job. Not for one-off browsing, creating an identity, or installing the toolchain.
license: MIT
---

# playwright-stealth-job

Turn one supervised browsing session into a file that replays for months. The model
authors the job once and repairs it when it breaks; every run in between is plain Python
against the same pinned identity — no DOM reading, no per-run model cost, and a manifest
afterwards saying exactly what happened.

```
jobs/<identity>/<job>.py                  COMMITTED — code, reviewable, no secrets
.stealth/<identity>/runs/<job>/<ts>/      gitignored — manifest, result, failure bundle
```

**Authorized use only.** A recorded job runs unattended, which raises the stakes. Automate
only properties you own or are authorized to test; honour Terms of Service and
`robots.txt`. Prefer read-only jobs. Full policy in
[references/secrets-and-artefacts.md](references/secrets-and-artefacts.md).

## When to use / when NOT to use

Use when a browsing task will happen **more than once**: a monthly invoice pull, a weekly
export, a recurring check. Also use to replay, list or repair an existing job.

**Not** for a one-off "go look at this page" — that is `playwright-stealth`, and driving it
directly is cheaper than recording it. **Not** for creating or retuning an identity
(`playwright-stealth-identity`) or installing the toolchain (`playwright-stealth-setup`).

Recording is a trade, not a free win: it swaps a steady per-run model cost for a spiky
breakage cost that lands the morning the vendor ships a redesign. Worth it for frequent
jobs on stable sites; not worth it for a quarterly job on a churny one. Say so rather than
recording everything by default.

## Workflow

1. **Scaffold.** The identity must already exist and be logged in.
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/job.py" --identity acme-portal --job invoices --new
   ```

2. **Drive it once, for real.** Open the identity and perform the task yourself, reading
   the actual pages — deep-link where you can, and note the role or label of every control
   you touch:
   ```python
   import os, sys
   sys.path.insert(0, os.path.join(os.environ["CLAUDE_SKILL_DIR"], "..", "playwright-stealth", "scripts"))
   from launch import open_identity
   with open_identity("acme-portal") as ctx:
       page = ctx.new_page(); page.goto("https://vendor.example/invoices")
   ```
   **Do not skip this and write selectors from a guess** — the job will fail on its first
   real run, after you have already paid for the model call.

3. **Write the steps into `run()`.** Use role and label locators; they are both the durable
   API and the humanized one. Set `START_URL` to deep-link past the menus, set
   `LOGGED_IN_WHEN` to something that only exists when signed in, and add at least one
   `h.expect(...)` outcome assertion.

4. **Lint, then run.** The linter is an AST allowlist over the house rules, not a style
   check, and it runs automatically before every replay:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/job.py" --identity acme-portal --job invoices --lint
   python3 "${CLAUDE_SKILL_DIR}/scripts/job.py" --identity acme-portal --job invoices --run
   ```
   Report the run directory and the seed. Later runs need only this command.

5. **On failure, label the page, then repair one step.** A failed run prints a
   `DIAGNOSE:` command; it runs offline and is always safe:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/pagestate.py" --run <run-dir> --offline
   ```
   Offline signatures (HTTP status, a few unmistakable titles, visible challenge iframes)
   catch the clear cases. For the rest, dropping `--offline` sends the failure page's
   redacted title and text to TypeSafe Jev for one typed label — **ask the user first**;
   a key in the environment is not permission. Either way `diagnosis.json` holds a label
   and the first thing to check: a suspected dead session, consent overlay, bot challenge
   or block (**do not re-run on a hunch**), site error, or content with no wall. It is a
   triage hint, not a verdict — confirm it in `failure.png` and `failure.html` before
   changing anything. Change only what broke, then re-run to prove it.

Other commands: `--list` (jobs and their last run), `--seed <n>` (reproduce a past run's
timing exactly), `--keep-trace`, `--self-test`.

## What "done" looks like

- `RESULT: ok` from a real `--run`, with the run directory and seed reported.
- The job file is committed-safe: no credentials, cookies, tokens or extracted data.
- It declares `LOGGED_IN_WHEN` and at least one outcome assertion.
- You told the user which identity it runs on and roughly what it will cost to maintain.

## Gotchas

- **Session expiry is the dominant failure mode, not selector drift.** Cookies expire, MFA
  re-prompts, consent banners return. `LOGGED_IN_WHEN` is what turns that into a clean
  early error instead of a confused click on a login wall. It is not optional.
- **The Locator API is fully humanized** — CloakBrowser patches `click`, `fill`, `type`,
  `hover`, `check`, `press`, `press_sequentially`, `select_option`, `drag_to`, `clear` and
  `scroll_into_view_if_needed` on the `Locator` class. So `page.get_by_role(...).click()` is
  durable *and* human-like. `page.query_selector()` returns an `ElementHandle`, which
  bypasses every patch — the linter rejects it.
- **Never runtime self-healing.** A "closest match" fallback converts a loud failure into a
  silent wrong click on a live authenticated account. Repair as a reviewable diff; never
  substitute a selector mid-run.
- **A fixed replay is a cross-run fingerprint.** `humanize` varies things within a run, not
  between runs. `h.pause()` jitters from the run seed, and the seed is recorded — so
  production runs differ while `--seed <n>` reproduces any past run exactly for debugging.
  Do not schedule replays at an identical wall-clock second.
- **Secrets are references, never values.** `h.secret("NAME")` reads
  `STEALTH_SECRET_NAME` at run time and fails fast naming the variable if unset. Better
  still, do not script the login at all — the profile already holds the session.
- **Run artefacts are secret-bearing.** Screenshots, traces and `result.json` capture
  account data and POSTed values. They live under gitignored `.stealth/` and are pruned;
  successful traces are deleted, failed ones never are.
- **State-changing steps are dangerous to replay.** The failure mode of a blind replay is
  doing it twice. Prefer read-only jobs; assert exactly-once on anything that submits,
  pays or deletes.
- **Never edit the linter to make a job pass.** Every rule maps to a documented tell.
- **`pagestate.py` is the only thing that ever calls a model, and only without
  `--offline`.** Replay never calls it, so "no model in the loop" stays literally true. The
  call sends the failure page's redacted title, at most 2,000 characters of its visible
  text (URLs, emails and digit runs masked), its password-field count and its visible
  iframe sources — never the page URL, cookies, field values or the key — and retries
  once on a 429/529. Page text can still steer the label (a hidden "you are signed in"
  note moved 13% of the probability in testing), which is why it is advisory: a label is
  a reason to look, never a reason to act.

## References

- [references/authoring.md](references/authoring.md) — compile-don't-record, selector
  durability ranking, assertions, the house-rule table, the repair workflow with the
  label-to-first-check table, run-to-run variance, and when not to record a job at all.
- [references/secrets-and-artefacts.md](references/secrets-and-artefacts.md) — the
  committed/gitignored split, the secret mechanism, why jobs should not log in, run
  artefacts, what leaves the machine and when, retention, and the traceability you get
  from git.
