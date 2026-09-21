# Authoring and repairing a job

How to turn one supervised browsing session into a file that replays for months, and what
to do the day it breaks.

**Contents:** [Compile, don't record](#compile-dont-record) ·
[Selector durability](#selector-durability) · [Assertions](#assertions) ·
[The house rules](#the-house-rules-and-why-the-linter-is-the-gate) ·
[Repair](#repair-when-a-job-breaks) ·
[Run-to-run variance](#run-to-run-variance-and-why-it-matters) ·
[When not to record](#when-not-to-record-a-job)

## Compile, don't record

There is no event interceptor here, and that is deliberate. A tape of clicks and
coordinates replays one execution; what you want is a *program* that expresses the intent.
So the flow is:

1. `--new` scaffolds the file with the contract already in place.
2. You drive the identity once, for real, and see what the pages actually contain.
3. You write the steps you just performed into `run()`, using what you saw.
4. `--lint` proves the file obeys the house rules; `--run` proves it works.

Step 2 is not optional. A job written from a guess at the DOM fails on its first real run,
and you will have spent the expensive model call anyway — just at the wrong moment.

The artefact is plain Python because the durable Playwright API (`get_by_role`,
`get_by_label`) is *also* the humanized one: CloakBrowser patches the entire `Locator`
class — `click`, `fill`, `type`, `hover`, `check`, `press`, `press_sequentially`,
`select_option`, `drag_to`, `clear`, `scroll_into_view_if_needed` — verified in its wrapper
source. Real code therefore buys durability, humanization, loops, pagination and
"this month" in one go. A declarative step schema would make the house-rule violations
unrepresentable, which is a genuine advantage, but it buys that by growing a second, worse
programming language the first time a job needs a conditional. The linter closes the gap
instead.

## Selector durability

Ranked, best first. Anything below the line dies on the next deploy.

| Prefer | Why |
| --- | --- |
| `page.get_by_role("link", name="Invoices")` | Accessible name and role survive CSS-in-JS hash churn and most A/B variants |
| `page.get_by_label("Email")` | Form labels are user-visible and rarely renamed |
| `page.get_by_text("Download all")` | Fine when the string is unique on the page |
| `page.locator('[data-testid="invoice-row"]')` | Only if the site actually ships test ids |
| ~~`nth-child`, generated class names, counted XPath, coordinates~~ | Regenerate on every build |

Two habits that beat any selector choice:

- **Deep-link instead of navigating.** Set `START_URL` to the page you need. Every menu you
  click through on the way is another selector that can break.
- **Scope to a business key, not a position.** `get_by_role("row").filter(has_text="2026-08")`
  survives reordering; "the third row" does not.

Where a site genuinely A/B-tests two layouts, write both as an explicit alternation the
author chose (`a.or_(b)`) and let the run log say which fired. That is not self-healing —
it is a declared branch.

## Assertions

A replay that "succeeds" while the site silently changed is worse than one that fails. Two
kinds of check earn their place; a third does not.

- **`LOGGED_IN_WHEN`** — checked by the runner before any step. This is the load-bearing
  one. Sessions expire, MFA re-prompts appear, consent banners come back; auth drift breaks
  replays far more often than selectors do, and a dead session is exactly where a blind
  replay does its most confusing damage (clicking "Download invoices" where the button is
  now "Sign in with Google").
- **At least one outcome assertion** via `h.expect(...)` — something true if and only if
  the job's actual goal succeeded. "The invoice table has ≥1 row." "The downloaded file
  parses and has records." "Text `Invoice #2026-08` is visible."
- **Not per-step assertions.** "This exact URL after step 3" is the brittleness trap.
  Assert on outcomes and identity; never on paths and positions.

## The house rules, and why the linter is the gate

`--lint` is an AST allowlist, not a style check. Each rule maps to a real, checkable tell:

| Rule | Level | Why |
| --- | --- | --- |
| `wait_for_timeout` | error | Emits CDP protocol traffic that behavioural scoring reads. Use `h.pause()`. |
| `query_selector` / `query_selector_all` | error | Returns an `ElementHandle`, which bypasses every humanize patch — the cursor teleports and keys fire without timing. |
| `evaluate` containing `scrollTo`/`scrollBy`/`.click(`/`dispatchEvent` | error | Synthesised input is `isTrusted: false` — a binary bot tell the canary also checks. |
| `evaluate` otherwise | warn | Fine for reading data out of a page, never for driving it. |
| String literal typed into a password/OTP-ish target | error | That is a credential in git. Use `h.secret("NAME")`. |
| `time.sleep(<constant>)` | warn | Identical gaps every run are a cross-run signature. Use `h.pause()`. |
| Missing `IDENTITY` / `LOGGED_IN_WHEN` / `run` | error | The runner's contract. |

The linter runs automatically before every `--run`. **Never edit the linter to make a job
pass** — the rules exist because each one burns an identity.

## Repair: when a job breaks

Breakage is normal and the workflow is designed around it. On failure the run directory
holds `failure.png`, `failure.html`, `url.txt`, `trace.zip` and a `manifest.json` naming
the exact step and error.

1. **Label the page first.** `pagestate.py --run <dir> --offline` writes `diagnosis.json`
   and says where to start; drop `--offline` for the TypeSafe pass only when the user has
   agreed to send the redacted excerpt. Session expiry is the more likely cause; check it
   first.

   | Label | First check |
   | --- | --- |
   | `login_or_mfa_wall` | Probably a dead session. Confirm in `failure.png`, re-run the hand-login (playwright-stealth step 3); the job is fine. |
   | `consent_overlay` | Deep-link past the dialog, or add one declared dismiss step. Not a selector miss until the dialog is gone. |
   | `bot_challenge_or_block` | **Do not re-run on a hunch.** Confirm in `failure.png`, then `canary.py --check-egress`, the proxy, then a human. Replaying into a wall is how an identity burns. |
   | `error_or_rate_limited` | The site failed or throttled. Wait, re-run once, then stop. Leave the job alone. |
   | `content_no_wall` | Proves neither a live session nor what moved. If `LOGGED_IN_WHEN` was found, suspect the failing locator and diff it against `failure.html`; if not, the marker itself may have moved. |
   | `unclassified` | No signature matched and no confident label. Read `failure.png` and `failure.html`. |

   Every label is a triage hint, never a verdict. The offline signatures match a few
   unmistakable pages (an anchored title, a 403/429/5xx, a visible challenge iframe) and
   can still be fooled by an article titled "Access Denied"; the Jev label is a
   probability over page text that the page itself can influence. Neither is a reason to
   rotate a proxy or re-baseline an identity by reflex — that creates the drift you are
   trying to avoid.
2. **Repair one step.** Hand the model the intent, the old selector and the failure-time
   DOM, and change only what broke. The rest of the job is still good.
3. **Ship it as a diff.** The job is committed, so the repair is a reviewable commit and
   the audit trail comes free.
4. **Re-run to prove it.** A repair that has not been replayed is a guess.

**Runtime self-healing is a trap here**, more so than in ordinary test automation. A
"closest match" heuristic turns a loud failure into a silent wrong click on a live,
authenticated account — the wrong invoice, the wrong toggle, a delete. Test suites tolerate
that because a human reads the report; an unattended replay does not. Healing that produces
a git diff is good. Healing that substitutes a selector at runtime is never acceptable.

## Run-to-run variance, and why it matters

`humanize` varies things *within* a run — Bezier cursor paths, per-character typing. It
does nothing about the fact that a replay does A→B→C with the same gaps, in the same order,
at 09:00 every Monday. Cross-run correlation is exactly what risk engines are good at, so a
naively fixed replay can be a *stronger* signal than a model improvising.

The resolution is to jitter the gaps and keep the logic fixed:

- `h.pause()` draws from the run's seeded RNG, so the delays differ every run.
- Step order and selectors stay fixed — that is what keeps a failure diff-able.
- Do not schedule replays at an identical wall-clock second.

Determinism is then a *replayable property*, not an executed one: every run records its
seed in `manifest.json`, and `--seed <n>` reproduces that run's timing exactly when you need
to debug it. You get variance in production and reproducibility on demand.

## When not to record a job

Recording trades a steady per-run model cost for a spiky breakage cost that lands at the
worst possible moment — the invoice pull that must happen today, on the morning the vendor
shipped a redesign. That is a good trade for a job that runs often against a stable site.
It is a bad trade for a job that runs quarterly against a site that churns, where
re-authoring under time pressure costs more than just letting the agent drive each time.

Record the frequent jobs. Leave the rare ones to the agent.
