---
name: playwright-stealth-identity
description: Creates or retunes one CloakBrowser stealth identity per account under .stealth/<name>/ — rolls a unique fingerprint seed, binds a proxy after preflighting it, fixes timezone and locale coherence. Use for setting up, creating, or reconfiguring a stealth identity or browser profile for an account. Not for installing the toolchain, or for browsing, logging in, or canary-testing a session.
license: MIT
---

# playwright-stealth-identity

Provisions the thing every stealth session needs: one identity per account, in
`<project>/.stealth/<name>/`, gitignored, with a fingerprint seed no other identity shares.

This skill is the **only** thing that creates or edits `profile.toml`. It hands the
finished identity to `playwright-stealth` for the hand-login and any driving.

**Authorized use only.** Identities are for properties you own or are authorized to test.
One identity per account, and never to evade a ban or run multiple accounts where that
breaks the site's terms. Full policy in
[references/identity.md](references/identity.md).

## Skip rule — read this before anything else

**The defaults are already the strongest anti-recognition settings.** Asking questions can
only move the user away from them. So:

> If the user said "defaults", "just set it up", or similar; **or** named no proxy,
> geography or locale; **or** you cannot ask them (non-interactive run, `/goal`, CI):
> **ask nothing.** Run the create command with no settings flags, report what you assumed,
> and stop.

That path is the common one. It ends in a single command and zero questions.

Never ask about the seed, file permissions, or the `.gitignore` entry — those are not user
decisions, the script handles them. Never offer a proxy the user did not raise: the host's
own residential IP beats anything they can buy, and a volunteered "proxy or not?" talks
people into a worse setup.

## When to use / when NOT to use

Use to create a new identity, or to retune an existing one — bind a proxy, correct a
timezone or locale, change the humanize preset, set a storage quota.

**Not** for installing the toolchain (`playwright-stealth-setup`), and **not** for
browsing, logging in, resuming a session or running the canary (`playwright-stealth`).
If the toolchain is missing, this skill's commands fail with a message naming the setup
skill — run that first, then come back.

## Workflow

1. **Name the identity.** Kebab-case, one per account, and it becomes the CLI handle:
   `acme-portal`, `reddit-main`, `eu-shop`. Derive it from what the user said; ask only
   if there is genuinely nothing to derive it from. **One identity per account is a hard
   rule** — the same seed plus the same IP links two accounts permanently.

2. **Decide whether to ask anything at all.** Apply the skip rule above. If the user *did*
   raise a proxy, a country, or a locale, ask **one batched message** covering only the
   rows below that their request actually touched, each with its default inline. Never a
   second round; anything unanswered takes the default.

   | Setting | Default | Ask only if the user … |
   | --- | --- | --- |
   | proxy | none — this machine's own residential IP | mentions a proxy, a country, or "different IP" |
   | timezone / locale | follows the host, or the proxy via geoip | mentions geography, language, or a mismatch |
   | human_preset | `careful` | mentions speed or says it feels slow |
   | storage_quota_mb | `5000` | mentions incognito detection |

3. **Preflight a proxy before binding it.** Never write an unverified proxy into a
   profile:
   ```bash
   python3 -m cloakbrowser info --proxy "http://user:pass@host:port"
   ```
   That prints the exit IP, timezone and locale a launch would actually apply. Check the
   geography is what the user asked for. If the timezone it reports is wrong, set
   `--timezone`/`--locale` explicitly in step 4 — they override geoip.

   Static ISP or static residential only. **Rotating pools break a logged-in session** and
   datacentre IPs are blocked on ASN reputation alone. Tiers and provider due diligence
   are in [references/identity.md](references/identity.md).

4. **Create it — one command, settings as flags.** Never hand-edit `profile.toml`; the
   flags substitute values into the commented template atomically:
   ```bash
   # the common case: no flags at all
   python3 "${CLAUDE_SKILL_DIR}/scripts/identity.py" --identity acme-portal --init

   # configured, when the interview produced answers
   python3 "${CLAUDE_SKILL_DIR}/scripts/identity.py" --identity eu-shop --init \
       --proxy "socks5://gate.example:1080" --proxy-user bob \
       --timezone Europe/London --locale en-GB
   ```
   The proxy **password** comes from `STEALTH_PROXY_PASSWORD` in the environment — never
   the file, never a command line where `ps` can read it. The same command **retunes** an
   existing identity: it applies the flags in place and keeps the seed, the comments, and
   every key you did not name.

5. **Show what it resolves to, then hand off.**
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/identity.py" --identity acme-portal --print-args
   ```
   Confirm `--fingerprint=<seed>` is present. Then tell the user the next step is the
   **playwright-stealth** skill for the hand-login and any driving — this skill does not
   open browsers.

## Output spec (what "done" looks like)

- `.stealth/<name>/profile.toml` exists with a non-zero seed, and `.stealth/` is
  gitignored.
- You reported: the identity name, the seed, the path, and every assumption you took.
- If a proxy was bound, you preflighted it first and said what exit IP it resolved to.
- You named `playwright-stealth` as the next step. You did not launch a browser.

## Gotchas

- **Zero questions is the success case, not a shortcut.** A user who says "defaults are
  fine" and gets a five-question interview has been failed by this skill.
- **One identity per account.** Never reuse a name, a seed, or a `user-data/` directory
  across two accounts. Two identities sharing a seed present the same device.
- **Retune, do not recreate.** Fixing a timezone means re-running `--init` with
  `--timezone`; it keeps the seed. Deleting the directory and starting over throws away
  the logged-in session and the fingerprint the site already knows.
- **`geoip` can be wrong.** It derives the timezone from a GeoIP database, and databases
  are stale: a real London exit IP has been observed setting the browser clock to
  `America/Chicago`. After binding any proxy, verify with the browsing skill's
  `canary.py --check-egress` and pin `timezone`/`locale` explicitly if they disagree.
- **A proxy is usually the wrong answer.** Rungs exist for a reason, and rung 0 — no proxy
  — is the strongest and cheapest. Only a different geography or a second non-linkable
  identity justifies one.
- **This skill installs nothing and opens nothing.** If `cloakbrowser` is not importable,
  the command says so and names `playwright-stealth-setup`.

## References

- [references/identity.md](references/identity.md) — the storage layout, every
  `profile.toml` key and its derivation rule, the proxy ladder and provider due diligence,
  identity hygiene, and the authorized-use policy in full.
