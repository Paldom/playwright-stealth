# Identity provisioning reference

Authoritative on what an identity *is*, what every `profile.toml` key means, and how those
keys become launch arguments. Verified 2026-08-25 against `cloakbrowser` 0.5.8 wrapper
source.

**Contents:** [Layout](#layout) · [Why the seed is mandatory](#why-the-seed-is-mandatory) ·
[Settings](#settings) · [Derivation rules](#derivation-rules) ·
[Interview policy](#interview-policy) · [Identity hygiene](#identity-hygiene) ·
[Authorized use](#authorized-use)

## Layout

```
<project>/
  .gitignore              <- must contain `.stealth/` before anything launches
  .stealth/
    README.md             <- "this directory holds live credentials"
    <identity>/           <- e.g. .stealth/acme-portal/
      profile.toml        <- settings; this skill writes it, the browsing skill reads it
      user-data/          <- CloakBrowser user_data_dir: cookies, logins, localStorage
      baseline.json       <- canary fingerprint baseline (browsing skill writes it)
      .lock               <- single-writer guard
```

Our files sit **beside** `user-data/`, never inside it — that tree belongs to Chromium,
which rewrites and prunes it. `--init` appends `.stealth/` to the project `.gitignore`
itself, creates directories `0700` and `profile.toml` `0600`, and every later run
re-checks with `git check-ignore`.

## Why the seed is mandatory

CloakBrowser is stealthy with zero flags: the binary generates a **random** fingerprint
seed at startup and derives canvas, WebGL, audio, fonts, client rects, GPU, hardware
concurrency, device memory and screen metrics from it. Verified in the wrapper source —
`get_default_stealth_args()` calls `random.randint(10000, 99999)` on every invocation.

For an anonymous one-shot fetch that is ideal. For a *logged-in* identity it is the
problem: every launch presents a different device to a site that already knows your
cookie, which is the classic re-verification trigger.

`--fingerprint=<seed>` fixes the device. `--init` rolls a seed that no sibling identity
already uses, because a shared seed gives two accounts the same device fingerprint and
links them permanently. `seed = 0` is a hard error at launch, never a silent fallback.

## Settings

| Key | Default | What it does |
| --- | --- | --- |
| `browser.headless` | `false` | Keep it false. Headless leaks GPU, display and animation-timing signals even with the C++ patches applied. |
| `browser.humanize` | `true` | Bézier cursor paths with overshoot, per-character typing with thinking pauses and self-corrected typos, accelerate/cruise/decelerate scrolling. Covers mouse, keyboard **and** scroll. |
| `browser.human_preset` | `"careful"` | `"careful"` (slower, deliberate, idle micro-movements between actions) or `"default"` (normal speed). Those are the only two values the wrapper accepts. |
| `identity.seed` | rolled by `--init` | `--fingerprint=<seed>`. The device identity. Never 0. |
| `identity.platform` | `""` | `""` leaves the wrapper's native default (`macos` on a Mac, `windows` on Linux). Setting it forces `--fingerprint-platform`. |
| `identity.storage_quota_mb` | `5000` | `--fingerprint-storage-quota`. The binary normalizes the quota whenever a seed is set, and quota-based incognito checks read the normalized value as private browsing. `0` leaves the binary default. |
| `network.proxy_server` | `""` | `""` uses this machine's own connection. Otherwise `http://host:port` or `socks5://host:port`. |
| `network.proxy_username` | `""` | Password comes from `STEALTH_PROXY_PASSWORD` only. |
| `network.proxy_bypass` | `""` | Hosts that skip the proxy. |
| `network.geoip` | `true` | Derives timezone and locale from the proxy exit IP. Applies only with a proxy. |
| `network.timezone` | `""` | e.g. `America/New_York`. Sets `--fingerprint-timezone`, a binary flag rather than CDP emulation, and overrides geoip. |
| `network.locale` | `""` | e.g. `en-US`. Sets `--lang` and `--fingerprint-locale`. Overrides geoip. |
| `network.webrtc_ip` | `"auto"` | `--fingerprint-webrtc-ip`. `auto` resolves the proxy exit IP so WebRTC cannot leak the real one. |
| `verify.canary_before_task` | `true` | The browsing skill runs the canary and refuses to hand over a session that fails it. |

Set these with flags on `identity.py --init`, not by hand-editing. The flags substitute
into the commented template atomically, keep every comment in its column, and validate the
value before writing. On an existing identity the same command retunes it in place,
keeping the seed and any key you did not name.

## Derivation rules

These live in `launch.py`'s `derive()` — a pure function with its own self-test. Knowing
them lets you predict a launch without running one (`--print-args` shows the resolved
result for a real profile).

- `seed <= 0`, non-integer, or a TOML boolean → **hard error**. Never launch unseeded.
- `platform == ""` → do not pass `--fingerprint-platform` at all. Coherence beats
  disguise: a Windows claim over Mac font metrics and a Mac GPU is a seam.
- `storage_quota_mb > 0` → pass `--fingerprint-storage-quota=<mb>`; otherwise warn that
  the normalized default can read as incognito.
- `proxy_server == ""` → build no proxy dict, pass `geoip=False` explicitly, and pass no
  WebRTC flag. With no proxy, `geoip=True` would resolve this machine's own public IP
  through third-party echo services (`ipify.org`, `checkip.amazonaws.com`) to tell the
  browser a timezone it already reports correctly, and the wrapper drops
  `--fingerprint-webrtc-ip=auto` with a warning anyway.
- `proxy_server != ""` → dict form `{"server", "username", "password", "bypass"}` with the
  password read from `STEALTH_PROXY_PASSWORD`; pass `geoip`; append
  `--fingerprint-webrtc-ip=<webrtc_ip>`.
- `timezone` / `locale` non-empty → pass them as wrapper parameters. They win over geoip by
  design, and they set binary flags rather than detectable CDP overrides.
- **Never** pass `stealth_args=False` — it disables every fingerprint flag.
- **Never** pass `user_agent`. A `--user-agent` override does not update Client Hints, and
  the resulting `Sec-CH-UA*` mismatch is trivially detected.

Two things the wrapper does that you cannot turn off: it passes `--no-sandbox`, and in
headed mode `--ignore-gpu-blocklist`. Removing them requires `stealth_args=False`, which
would remove the fingerprint patches too.

## Interview policy

The defaults above are already the strongest anti-recognition settings, so every question
asked is an opportunity to make the identity worse. The policy that follows from that:

- **Default to asking nothing.** "Defaults are fine", no stated preference, or a
  non-interactive run all mean: create with no flags, report the assumptions, stop.
- **Pre-fill from what the user already said.** "A German IP for my shop account" is
  already name + geography + proxy intent. Do not re-ask it.
- **One batched question, never a second round.** Every row gets its default inline;
  anything unanswered takes the default.
- **Never ask about the seed, permissions, or the gitignore entry.** Not user decisions.
- **Never volunteer a proxy.** Rung 0 — the host's own residential IP — is the strongest
  option, and offering "proxy or not?" as a neutral choice talks people into a worse
  setup. Only a different geography or a non-linkable second identity justifies one.

## Identity hygiene

- **One identity per account.** Same seed plus same IP links two accounts permanently. A
  new account gets a new `.stealth/<identity>/`.
- **Retune, do not recreate.** Fixing a timezone keeps the seed and the logged-in session.
  Deleting the directory throws away both, and the site sees a brand-new device.
- **The profile is credential material.** `user-data/` holds decryptable session cookies
  and saved passwords. Never commit it, never copy it between machines, never share it.
  On macOS the cookie encryption is Keychain-bound, so a copied profile often will not
  decrypt anyway.
- **A blank slate is itself a signal.** A brand-new profile that logs in and immediately
  performs a high-stakes action looks unlike a real user, who arrives with history.
- **Sessions still expire server-side.** A pinned identity reduces re-verification; it does
  not make logins immortal.

## Authorized use

Provision identities only for properties you own or are authorized to test. Honor Terms of
Service and `robots.txt`. No fraud, credential abuse, mass account creation,
denial-of-service, or defeating access controls you have no right to bypass. Creating
multiple identities to evade a ban, or to run multiple accounts where the site's terms
forbid it, is outside what this is for. You are responsible for how you use it.
