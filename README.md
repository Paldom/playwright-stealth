<p align="center">
  <img src="assets/icon.svg" alt="playwright-stealth icon" width="128"/>
</p>

# Playwright Stealth

[![CI](https://github.com/Paldom/playwright-stealth/actions/workflows/ci.yml/badge.svg)](https://github.com/Paldom/playwright-stealth/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![skills.sh](https://skills.sh/b/Paldom/playwright-stealth)](https://skills.sh/Paldom/playwright-stealth)

Agent Skills for stealth browser automation with CloakBrowser and Playwright — a C++-patched Chromium that evades bot detection, driven headful on a persistent profile so hand-made logins survive across agent sessions.

Agent Skills for [Claude Code](https://code.claude.com/docs/en/skills) (and any
[Agent Skills](https://agentskills.io)-compatible tool). Each skill is a folder under
[`skills/`](skills/) with a single-purpose `SKILL.md`, trigger evals, and optional
scripts/references — validated on every write, commit, and PR.

## Quick start

Install with the [skills CLI](https://skills.sh) — auto-detects 70+ agents
(Claude Code, Codex, Cursor, Copilot, pi, …):

```bash
npx skills add Paldom/playwright-stealth                  # all detected agents
npx skills add Paldom/playwright-stealth -a codex -a pi   # or target specific agents
```

Or with the [GitHub CLI](https://cli.github.com/manual/gh_skill_install) (≥ 2.90),
including version-pinned installs from releases:

```bash
gh skill install Paldom/playwright-stealth
gh skill install Paldom/playwright-stealth <skill> --pin <tag>
```

Or as a Claude Code plugin:

```
/plugin marketplace add Paldom/playwright-stealth
/plugin install playwright-stealth@playwright-stealth
```

Or copy a single skill into a project:

```bash
git clone https://github.com/Paldom/playwright-stealth.git
cp -r playwright-stealth/skills/<skill-name> your-project/.claude/skills/
```

Then just describe the task — the skill activates on its description — or invoke it
explicitly with `/<skill-name>`.

## Skills

| Skill | What it does |
| --- | --- |
| [`playwright-stealth-setup`](skills/playwright-stealth-setup/) | Installs and verifies the CloakBrowser toolchain — pip package, patched Chromium binary, licence key, macOS unquarantine — and reports which build will actually launch. Once per machine. |
| [`playwright-stealth-identity`](skills/playwright-stealth-identity/) | Creates one identity per account with its own pinned fingerprint seed, binding a proxy only if you actually need one. Takes defaults silently when you say so. Once per account. |
| [`playwright-stealth`](skills/playwright-stealth/) | Runs a task in a headful, humanized session on an existing identity, so a hand-made login keeps working in later runs. Self-verifies against a fingerprint baseline. |
| [`playwright-stealth-job`](skills/playwright-stealth-job/) | Records a browsing task as a committed Python job, then replays it with no model in the loop — lint-gated, seed-jittered, with a run manifest and failure bundle. |

They run in that order: install once, provision per account, then browse as often as you
like, and promote anything repetitive into a job.

Setting this up in a project for the first time? Paste
[docs/setup-prompt.md](docs/setup-prompt.md) into your agent — it orders the skills and
gates each one on its own verifier.

## Choosing a proxy

> **Start here: you probably don't need one.** A home or office connection is already a
> genuine residential IP with real ISP reputation and years of history. Nothing you can buy
> beats it, it costs nothing, and it adds no failure modes — which is why no proxy is the
> default. Buy one only when you need a **different country**, or a **second identity that
> must not share an IP with the first**.

### What a proxy has to be

The wrong proxy is worse than no proxy: it swaps a trusted residential IP for a flagged
one. Every row below is a hard requirement for a logged-in identity, not a preference.

| Must be | Not | Why |
| --- | --- | --- |
| **Residential or ISP** ASN | Datacenter | ASN reputation is scored first and cheapest. A hosting ASN is blocked before any of your fingerprint work is even evaluated. |
| **Dedicated** to you | Shared pool | On a shared IP you inherit every other tenant's blocks and bans, and you can't tell when it happened. |
| **Static**, held for weeks | Rotating / sticky-for-30-min | An IP that changes under a live login is the classic re-verification trigger. Rotation is for anonymous collection, never for an identity. |
| **CONNECT-only** or SOCKS5 | TLS-terminating | A proxy that terminates TLS destroys the genuine Chrome JA3/JA4 handshake — one of this stack's biggest free wins. |
| **One IP per identity** | One IP for several accounts | Same IP plus same fingerprint links the accounts permanently. |

Two traps worth knowing before you spend anything:

- **"Fake ISP" mislabeling is reported at every price tier, including premium** — datacenter
  subnets leased through brokers, relabelled residential. They pass a superficial ASN name
  check and fail real fraud scoring. Verify the IP yourself during the trial window; don't
  trust the product name.
- **Cheap residential pools carry documented botnet supply-chain risk.** FBI and Bitsight
  reporting in 2026 tied parts of the commercial residential pool to compromised consumer
  devices. That is a legal and ethical exposure, not just a quality one.

And the ceiling on all of it: **no IP alone beats a modern anti-bot stack.** A clean
residential address in front of an unmasked browser still gets caught.

### How to set one up

Preflight before you bind anything, so you buy a proxy you have actually seen work:

```bash
python3 -m cloakbrowser info --proxy "http://user:pass@host:port"
```

That prints the exit IP, timezone and locale a real launch would apply. Then attach it to
an identity with flags — never by hand-editing `profile.toml`:

```bash
python3 skills/playwright-stealth-identity/scripts/identity.py \
    --identity eu-shop --init \
    --proxy "http://host:port" --proxy-user "<user>" \
    --timezone Europe/London --locale en-GB
```

Then verify it end to end. This is the step people skip:

```bash
# the password lives in the environment — never in profile.toml, never in argv
export STEALTH_PROXY_PASSWORD='…'
python3 skills/playwright-stealth/scripts/canary.py --identity eu-shop --check-egress
```

It compares the browser's clock against the live exit IP. It exists because a real London
exit IP was observed setting the browser to `America/Chicago` — a US clock on a UK address
is a louder signal than using no proxy at all. Setting `--timezone`/`--locale` explicitly
overrides the automatic lookup and fixes it.

### Providers

> **Disclosure:** these are affiliate links — if you sign up through one, this project may
> earn a commission at no extra cost to you. **The list is not exhaustive and the ordering
> is not a quality ranking**; the requirements above apply to every provider, linked or not,
> and where one is worth avoiding that is said plainly regardless of whether the link earns
> anything. Other capable providers (Decodo, Oxylabs, and more) are compared by measurable
> properties in the reference below.

| Provider | Product | Shape |
| --- | --- | --- |
| [Webshare](https://www.webshare.io/?referral_code=zj5g181wgi7g) | Static ISP + datacenter | Cheapest sticker price at volume; free tier gives 10 datacenter IPs. Measured 2026-08-25: a free-tier IP egressed from `AS20738 Heart Internet` — a hosting ASN, so treat the free tier as datacenter, not residential |
| [IPRoyal](https://iproyal.com/?r=1623337) | Static residential | Budget; non-expiring traffic. Carries "fake ISP" complaints — fraud-score any IP during the trial window before committing |
| [NodeMaven](https://nodemaven.com/?ref_id=1232716d) | Dedicated static ISP | Mid-tier, flat per-IP pricing with no bandwidth metering on dedicated IPs |

Described by measurable properties — static vs rotating, ASN, session duration, bandwidth
caps — rather than ranked. The full ladder (including static mobile for the hardest
targets), the provider due diligence, and the one provider this project will not link are
in
[playwright-stealth-identity/references/proxies.md](skills/playwright-stealth-identity/references/proxies.md).

## Repository structure

```
skills/                  # distributed skills, one folder per skill (SKILL.md + evals/ + scripts/)
docs/                    # skill-authoring guide, eval methodology, deployment guide
scripts/                 # deterministic validator used by hooks and CI
skills.sh.json           # skills.sh repo-page customization (groupings)
.claude/                 # agentic dev setup: hooks + bundled add-skill / publish-repo skills
.claude-plugin/          # plugin + marketplace manifests (makes this repo installable)
.local/                  # gitignored working area: sources, research, PROMPT.md (see below)
```

## Working on this repo with an agent

This repo is agent-native: canonical agent instructions live in
[AGENTS.md](AGENTS.md) (CLAUDE.md imports it), hooks validate every `SKILL.md` on
write, `make check` runs the full validator, and CI enforces the same gate on every
PR. The bundled `add-skill` skill walks the eval-first authoring workflow described
in [docs/skill-authoring.md](docs/skill-authoring.md). Maintainers drive sessions
with their own (gitignored, personal) `.local/PROMPT.md` goal prompt.

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the skill-proposal
process, the authoring workflow, and the PR checklist. Please note the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Support

Questions, ideas, or something not working? Start with [SUPPORT.md](SUPPORT.md) —
bugs and skill proposals have [issue templates](../../issues/new/choose), and
security concerns go through [SECURITY.md](SECURITY.md) (never a public issue).

## License

[MIT](LICENSE) © 2026 Paldom
