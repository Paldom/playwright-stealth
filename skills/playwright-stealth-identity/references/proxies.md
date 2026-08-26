# Choosing an egress for a long-lived identity

The IP is the highest-leverage variable in this whole stack, ahead of every fingerprint
patch. Anti-bot systems are sequential gates and IP/ASN reputation is evaluated first and
cheaply — on a clean residential IP it stops being the binding constraint, which is
exactly why a home connection makes everything downstream cheaper.

This is a ladder. **Stop at the first rung that fits.**

**Contents:** [Rung 0 — no proxy](#rung-0--no-proxy-the-default-and-usually-the-best) ·
[Rung 1 — static ISP](#rung-1--static-isp-residential) ·
[Rung 2 — static mobile](#rung-2--static-mobile) ·
[What not to use](#what-not-to-use) · [Hard rules](#hard-rules) ·
[Preflight](#preflight-before-binding-a-proxy-to-an-identity) ·
[Provider due diligence](#provider-due-diligence)

## Rung 0 — no proxy. The default, and usually the best

`proxy_server = ""` means "use this machine's own connection". A home or office line is a
genuine residential IP with real ISP reputation and real history. No purchased proxy
beats it, it costs nothing, and it adds no failure modes — no extra hop, no auth, no
bandwidth cap, no third-party operator between you and the site.

Use a proxy only when you need a **different geography**, or a **second identity that
must not share an IP with the first**.

## Rung 1 — static ISP / "static residential"

One dedicated IP per identity, held for weeks or months. This is the right tool for a
long-lived logged-in session: ISP-registered addresses hosted in a datacentre, so you get
residential ASN trust with datacentre stability.

Products in this category, with their measurable properties rather than a ranking:

| Product | Coverage | Caps worth knowing |
| --- | --- | --- |
| Oxylabs "Static Residential" | 17 countries, SOCKS5/UDP | concurrency drops past 50 GB/IP shared, 100 GB/IP dedicated |
| NodeMaven dedicated | flat per-IP, mid-tier | no bandwidth metering on dedicated IPs |
| Decodo static residential | value tier | the most consistent organic sentiment on r/proxies |

If budget is the binding constraint, Decodo (formerly Smartproxy) benchmarks close to the
premium providers on success rate at a fraction of their per-GB price — confirm it offers
a genuinely *static* product before binding one to an identity.

Vendors enter and leave this market, and entry tiers get withdrawn without notice. Check
the product still exists and still sells single static IPs before planning around it.

## Rung 2 — static mobile

A carrier 3G/4G/5G IP, for the hardest targets only. Trust is highest because these are
real carrier CGNAT ranges shared with real humans, and social platforms weight mobile
heavily. It is also the most expensive rung. The survival-rate figures that circulate
(95%+ mobile vs 70–85% ISP) come from mobile-proxy vendors — treat them as directional,
not as measurements.

## What not to use

- **Rotating residential — not for an identity.** Sticky sessions typically last 10–30
  minutes (Oxylabs tops out around 24 h) and the IP can drop when the underlying device
  goes offline. An IP that changes under a logged-in session is the classic
  re-verification trigger. Rotating residential is fine for anonymous collection running
  *alongside* an identity; never for the identity itself.
- **Datacentre proxies — never.** Reputation alone gets them blocked before any of your
  fingerprint work is evaluated.
- **TLS-terminating proxies — never.** A proxy that terminates TLS destroys the genuine
  Chrome JA3/JA4/HTTP2 fingerprint, which is one of this stack's biggest free wins.
  Accept CONNECT-only or SOCKS5 proxies and nothing else.

## Hard rules

- **One IP per identity, and never change it.** The hand-made login and every later agent
  run must egress from the same address. A country hop mid-identity forces
  re-verification. Never rotate mid-session on a logged-in profile.
- **Always pair a proxy with `geoip = true`, or set `timezone`/`locale` explicitly.** A US
  exit IP with a Budapest timezone is a stronger tell than any default value. Explicit
  `timezone`/`locale` set binary flags rather than CDP overrides, so prefer them once you
  know the right values — they are deterministic and win over geoip.
- **Do not leak the real IP over WebRTC.** `webrtc_ip = "auto"` resolves the proxy exit IP
  and rewrites ICE candidates. A leaked real IP defeats everything upstream of it.
- **Passwords come from `STEALTH_PROXY_PASSWORD`.** Never in `profile.toml`, never on a
  command line where `ps` can read it.
- **Vet before buying:** confirm the ASN really belongs to an ISP, that latency is stable
  (under ~100 ms), and that the IP is not already on blocklists from a previous tenant.

## Preflight before binding a proxy to an identity

```bash
python3 -m cloakbrowser info --proxy "http://user:pass@host:port"
```

This resolves and prints the exit IP, timezone and locale a real launch would apply
through that proxy — the same resolution `geoip=True` performs. Run it *before* editing
`profile.toml`, and check that the geography it reports is the one you intended. If the
timezone it returns is not what you want the browser to claim, set `timezone`/`locale`
explicitly rather than hoping geoip guesses right.

Then edit `profile.toml`, and re-run the canary: it compares the browser's timezone
against the configured value and reports a mismatch.

## Provider due diligence

- **NetNut: do not recommend.** The FBI and IRS-CI seized its domains on 2 July 2026 over
  alleged ties to a roughly 2M-device botnet. Any article praising it that predates July
  2026 is stale.
- **Ask how the pool was built, including at the premium end.** Researchers published
  findings in 2026 on a major provider's residential pool being assembled partly via an SDK
  embedded in smart-TV apps. Price tier is not a proxy for provenance.
- **Sourcing ethics are a real risk axis, not PR.** Takedowns through 2025–2026 confirmed
  that meaningful portions of "residential" inventory come from compromised devices and
  malicious SDKs. Buying that inventory is a choice with consequences beyond uptime.
- **The review space is almost entirely affiliate content.** Name products and their
  measurable properties — static vs rotating, ASN, session duration, bandwidth caps — and
  do not reproduce vendor pass-rate claims as fact or rank providers by "best".
