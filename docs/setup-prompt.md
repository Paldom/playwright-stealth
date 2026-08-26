# Setup prompt

A paste-ready goal for setting stealth browsing up in a project for the first time. It
orders the two skills, gates each step on its own verifier, and leaves every change in the
working tree for you to review — it never commits.

**Before pasting:** replace `<IDENTITY>` (a kebab-case name, one per account),
`<SITE>` and `<LOGIN_URL>`. Run it from the project root — `.stealth/` is created there.

```
/goal Set up stealth browsing in this project with the playwright-stealth skills, and stop only when every gate below passes. Work autonomously. Never run git commit or git push - leave every change in the working tree for me to review.

Target: one browsing identity named <IDENTITY> for <SITE>, which I will log into by hand once and you will reuse in later runs.

Order of work - do not skip or reorder:

1. Toolchain. Load the playwright-stealth-setup skill.
   Gate: `python3 "${CLAUDE_SKILL_DIR}/scripts/setup_check.py"` prints `RESULT: ok` (exit 0) with `STATUS binary: OK` and `STATUS launch: OK`. Run it with `--install` only if the probe reported something missing; on macOS an "app is damaged" launch failure means `--fix-quarantine`.
   Report the wrapper version, the Chromium version that will actually launch, and the licence tier - all read from the live diagnostic, never from documentation. State plainly that the binary is closed-source, that it runs with the Chromium sandbox disabled, and that a keyless build trails current Chrome.
   Do NOT create an identity or launch a browsing session in this step.

2. Identity. Load the playwright-stealth-identity skill.
   `python3 "${CLAUDE_SKILL_DIR}/scripts/identity.py" --identity <IDENTITY> --init`
   Ask me nothing unless I named a proxy, a country or a locale - the defaults are already the strongest settings. Gate: `RESULT: ok`, `.stealth/` now appears in this project's .gitignore, and the printed seed is non-zero. Report the seed and the path.
   Then run the same script with `--print-args` and confirm `--fingerprint=<that seed>` is in the resolved args. If it is not, stop and tell me.

3. Profile. Show me `.stealth/<IDENTITY>/profile.toml`. If I want a proxy, apply it with the identity skill's flags rather than hand-editing - preflight it first with `python3 -m cloakbrowser info --proxy <url>`, take the password from STEALTH_PROXY_PASSWORD, and verify afterwards with the canary's `--check-egress`.

4. Hand login. `python3 "${CLAUDE_SKILL_DIR}/scripts/launch.py" --identity <IDENTITY> --url <LOGIN_URL>`
   Hand me the window and wait. Do not type my credentials. Remind me to tick "remember me" / "keep me signed in" - without it the site issues a session-only cookie and the login dies when the browser closes, however well the profile persists. I press Enter in the terminal when done, which closes the browser and flushes the profile.

5. Baseline. `python3 "${CLAUDE_SKILL_DIR}/scripts/canary.py" --identity <IDENTITY>`
   Gate: `RESULT: ok` and `.stealth/<IDENTITY>/baseline.json` now exists.

6. Prove the resume. Relaunch the same identity, confirm <SITE> still shows me logged in, close it, and run the canary again.
   Gate: `RESULT: ok` with "canvas hash matches baseline" PASS. Then actually load a signed-in-only page to prove the session resumed - the canary never visits target sites, so it cannot check that for me. If step 4 printed a `NOTE:` about session-scoped cookies for <SITE>, the login will not survive and I need to redo it with "remember me" ticked.

Rules:
- Never edit, weaken or skip a check to get a green result. A `RESULT: fail` is a real failure: read the detail and fix the cause.
- Never open one identity from two processes. If you need concurrency, create a second identity.
- One identity per account. Never reuse a seed or a profile across two accounts.
- `.stealth/` holds live session credentials. Never commit it, never print its cookies, never copy it elsewhere.
- Automate only properties I own or am authorized to test.

Definition of done: all six gates passed with the relevant command output quoted, the identity name, seed and path reported, and a list of exactly which files in this project changed.
```

## Notes

- `${CLAUDE_SKILL_DIR}` resolves per skill, so the same variable points at
  `playwright-stealth-setup/` in step 1 and `playwright-stealth/` from step 2 on. Outside
  Claude Code, substitute the installed skill paths.
- Steps 3 and 4 deliberately stop for a human. An agent should never enter your
  credentials, and the profile only reaches disk after the browser closes cleanly.
- Step 6 is the one that proves the design works. A passing canvas-hash check means the
  fingerprint seed is holding; a surviving marker cookie means the persistent profile is
  round-tripping to disk. Without both, a "durable login" is not durable.
- Adding a second account later is just step 2 onward with a new `<IDENTITY>`.
