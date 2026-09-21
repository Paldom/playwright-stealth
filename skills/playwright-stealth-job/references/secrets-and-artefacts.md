# Secrets, run artefacts and traceability

What may be committed, what must never be, and what a run leaves behind.

**Contents:** [The split](#the-split-committed-code-gitignored-everything-else) ·
[Secrets](#secrets) · [Why jobs should not log in](#why-jobs-should-not-log-in) ·
[Run artefacts](#run-artefacts) · [What leaves the machine](#what-leaves-the-machine) ·
[Retention](#retention) ·
[Traceability](#traceability) · [Authorized use](#authorized-use)

## The split: committed code, gitignored everything else

```
<project>/
  jobs/<identity>/<job>.py                     COMMITTED — code, reviewable, no secrets
  .stealth/<identity>/
    user-data/                                 gitignored — cookies, saved logins
    runs/<job>/<timestamp>/
      manifest.json                            seed, sha, steps, outcome
      result.json                              whatever the job returned
      failure.png / failure.html / url.txt     failures only
      failure.json                             failures only — title, url, status, visible text
      diagnosis.json                           written by pagestate.py --run, on request
      trace.zip                                failures only, unless --keep-trace
```

The rule that makes this work: **a job file must be safe to read in a pull request.** No
credential values, no cookies, no tokens, no extracted data, no URLs carrying a session id
or one-time token.

Committing the jobs and gitignoring `.stealth/` is not an arbitrary split — it *forces* the
discipline. Put jobs inside `.stealth/` and passwords quietly rot into them, because
nothing is watching. Keep them in git and every secret becomes a visible diff.

## Secrets

The mechanism is a reference, never a value:

```python
page.get_by_label("Password").fill(h.secret("ACME_PW"))   # reads STEALTH_SECRET_ACME_PW
```

- `h.secret(name)` resolves `STEALTH_SECRET_<NAME>` from the environment at run time.
- If the variable is unset it **fails fast and names the variable**. It never returns an
  empty string — typing empty into a credential field is how accounts get locked out.
- Declare the names in `REQUIRES_SECRETS` so a reader knows what the job needs. Listing a
  name exposes nothing.

The linter rejects a string literal typed into any target whose expression mentions
password, passwd, pwd, otp, one-time, 2fa, mfa, pin, secret or token. That catches the
common case; it is a tripwire, not a proof. A credential typed into a mislabelled field
will slip past any regex, which is why the public-repo rule is the real control and a
secret-scanning pre-commit hook over `jobs/` is worth adding.

Environment variables are the honest v1. They are a *backend*, not the secret model — the
job only ever names a secret, so swapping in an OS keychain or a vault later changes one
function and no job files.

Never in a job file, under any circumstances: passwords, current OTPs, TOTP seeds, recovery
codes, API tokens, `Authorization` headers, cookie or storage-state dumps, signed URLs,
proxy passwords, licence keys.

There is one leak channel that git hooks cannot see: **the authoring conversation itself**.
If a credential is pasted into the chat while a job is being written, it is in the
transcript regardless of how clean the file is. Type credentials into the browser window
by hand; never into the agent.

## Why jobs should not log in

The persistent profile already holds the session. `user-data/` carries cookies and saved
logins across restarts — that is the entire premise of the identity design, and the canary
proves it every run with the marker-cookie check.

So the normal shape of a job is **assert logged in, then work**:

```python
LOGGED_IN_WHEN = 'role=link[name="Sign out"]'
```

The runner checks it before any step and stops with an actionable error if it fails. That
removes the highest-risk step from essentially every run, and it makes the dominant failure
mode — auth drift — loud and early instead of silent and confusing.

Scripted login is the exception, for the day a session genuinely expires. Even then, prefer
re-running the hand-login: it is one command, it keeps credentials out of the toolchain
entirely, and MFA usually needs a human anyway.

## Run artefacts

Every run writes a directory under `.stealth/<identity>/runs/<job>/<timestamp>/`.

`manifest.json` is the record: identity, job, ok, **seed**, `job_sha256`, duration, error,
the HTTP status of the last document load, and the ordered step log. The seed is what
makes a past run reproducible; the sha is what tells you which version of the job
produced it.

**Run artefacts are secret-bearing.** A screenshot catches whatever was on screen — an
account name, an invoice total, a password manager dropdown. A trace embeds DOM snapshots
and network data including POSTed form values. `result.json` is the extracted data itself,
which is often the most sensitive thing in the whole tree. All of it lives under `.stealth/`
and is gitignored for that reason. Do not move it out.

## What leaves the machine

Replay makes no model call: the only network traffic a `--run` produces is the browser's
own, through whatever proxy the identity binds. The one thing in this skill that talks to
a model is `pagestate.py --run <dir>` without `--offline`, which you invoke by hand on a
failed run after the user has agreed to it. If its offline signatures do not match and
`TYPESAFE_API_KEY` is set, it sends one request (retried once on a 429/529) to
`api.typesafe.ai` carrying the failure page's title and at most 2,000 characters of its
visible text — URLs, emails and runs of four or more digits masked — plus its
password-field count and the host and first path segment of each visible iframe. It
refuses redirects, so the key cannot follow one elsewhere. It never sends the page URL,
cookies, field values, the screenshot or the HTML, and it never runs on a successful run.
`diagnosis.json` stores the label, source, probabilities, confidence, model version and
HTTP status — not the text or the title. TypeSafe states it does not train on requests;
its retention terms are its own, so treat the call as sharing a redacted excerpt with a
third party and skip it for a page you would not paste into a support ticket.

## Retention

- **Successful runs**: `trace.zip` is deleted on success (`--keep-trace` overrides). A
  successful trace has a shelf life of about a day.
- **Failed runs**: everything is kept, and pruning never touches them. The failure bundle
  is the entire debugging story.
- **Pruning**: the oldest successful runs beyond the newest 10 are removed automatically,
  and the run reports how many it dropped.

## Traceability

Because jobs are committed code and every run records the sha it executed, the audit trail
is free and needs no extra machinery:

- `git log jobs/<identity>/<job>.py` — what changed, when, and why.
- `git blame` — which repair introduced which selector.
- `manifest.json` `job_sha256` — exactly which version produced a given result.
- `--seed <n>` from an old manifest — reproduce that run's timing precisely.

This is why the jobs are not gitignored. Hiding them would throw the audit trail away for
no benefit, since they contain nothing secret.

## Authorized use

A recorded job runs unattended, which raises the stakes rather than lowering them. Automate
only properties you own or are authorized to test; honour Terms of Service and `robots.txt`.
Prefer read-only jobs. Any step that changes state — submitting a form, paying, deleting —
deserves an explicit assertion that it happened exactly once, because the failure mode of a
blind replay is doing it twice.
