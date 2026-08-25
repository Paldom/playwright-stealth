# Why three skills, and the case against

Three skills share the `playwright-stealth` prefix. They split by **lifecycle**, and each
owns exactly one artifact:

| Skill | Scope | Owns | Runs |
| --- | --- | --- | --- |
| `playwright-stealth-setup` | machine | `~/.cloakbrowser/` — the binary, the licence key | once per machine |
| `playwright-stealth-identity` | account | `.stealth/<name>/profile.toml` | once per account, plus retunes |
| `playwright-stealth` | session | the browser, the lock, `baseline.json` | every task |

Only the identity skill writes `profile.toml`. Only the browsing skill opens a browser.
Neither installs anything.

## The case against the identity split

The third skill was added on 2026-08-25 over the objection of most of an `/advise-max`
panel. Recording that here so nobody re-derives it from scratch — and so the predicted
failure modes get checked rather than forgotten.

Four flagship models were asked whether identity provisioning justified its own skill.
**Three said no** (Claude Fable 5 @max, Grok 4.6 @xhigh, Kimi K3); one said yes
(GPT-5.6 Sol @ultra), conditional on the new skill becoming the *sole* owner of
provisioning. A fifth (Gemini 3.7) was out of quota.

The strongest objection, and it is a real one:

> The first "browse this site like a human" **is** identity creation. Machine-vs-project
> was a clean split because installing never happens mid-browse; identity-vs-browse is not
> analogous. A third skill puts a router hop on the happy path.

Concretely, three predicted regressions:

1. **First-browse breaks.** Making the descriptions disjoint requires stripping "create a
   stealth identity" from the browsing skill. A user with no `.stealth/` who says "log into
   X like a human" now needs two skills to route in sequence, and agents drop the second.
2. **Prefix pull gets worse.** Two independent reviewers had already predicted "install
   playwright and its browsers" would misroute to the setup skill until an explicit
   exclusion was added. A third `playwright-*` name adds surface.
3. **Duplicated guidance drifts.** Two documents describing the same `profile.toml` keys
   diverge the first time a default changes.

## What was done about each

1. **First-browse** — `launch.py`'s "no identity" error names the identity skill and its
   exact command, so an agent that routed straight to browsing is told where provisioning
   lives instead of improvising a profile. The hand-off is in the error path, not left to
   the router. This is a mitigation, not a fix: it still costs a turn.
2. **Prefix pull** — all three descriptions carry explicit exclusions naming the other
   two's territory, and `playwright-stealth-setup` gained "creating an identity or profile"
   to its exclusion because "set up cloakbrowser" and "set up a stealth identity" are a
   genuine near-collision.
3. **Drift** — there is one implementation, not two. `identity.py` resolves the sibling
   skill's `launch.py` and calls into it; it fails loudly if the browsing skill is not
   installed alongside. `references/proxies.md` moved rather than being copied, and
   `references/profile.md` was cut down to what the browsing skill actually owns.

## What to watch

If any of these show up, the split is not paying for itself and folding the interview back
into `playwright-stealth` is the correct response:

- Users routinely asking to browse and getting told to provision first.
- The identity skill triggering on "set up cloakbrowser" or the setup skill triggering on
  "set up an identity".
- `references/identity.md` and `references/profile.md` disagreeing about a key.

The full panel transcript is in the maintainer's gitignored `.local/`; it is not committed
because it contains no verifiable claim the skills do not already state.
