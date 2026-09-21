# Why Jev is a post-failure label and nothing more

TypeSafe's Jev (released 2026-09-15) is a typed decision model: given a state and typed
questions it returns a choice with probabilities, a score, or a yes/no probability, never
text, in a few hundred milliseconds at $0.042 per million input tokens. Launch-week demos
(`browser-use/jev-ultrafast`, `jkudish/jev-browser`) use it to pick the next browser
action from a numbered element table. The question for this repo was where, if anywhere,
that helps a stealth identity that is meant to be reused for weeks.

Three candidates went to `/advise-max` on 2026-09-22 with the verified API facts, TypeSafe's
own jaggedness page, the independent rtrvr.ai benchmark and the house rules attached. Two
of five members answered (Claude Fable 5.1 at max effort, Codex GPT-6 Astra at ultra);
Gemini, Grok and Kimi failed on quota or setup. The two converged.

| Candidate | Verdict | Why |
| --- | --- | --- |
| **A.** Page-state classifier | **Ship, reduced:** opt-in, advisory, after a run has failed, on redacted text, never as a gate | Both: a label on a captured failure page is cheap triage. Neither accepted it as proof of a live session or as a pre-step gate — the `LOGGED_IN_WHEN` selector already stops a run when its marker is missing, and a Jev call in the happy path puts a model and a network dependency back into a replay whose whole promise is that it has neither. |
| **B.** Jev-driven navigation loop for one-off tasks | **Reject** | The one-off skill already has the strongest classifier in the loop: the agent. Jev's edge is per-step latency, which is invisible under humanized pacing. A "read-only goal" is not enforceable by vocabulary — a click is a click — and rtrvr's `Send` at p=0.95 with no effect, plus the 65–73% injection sway measured on Reddit, are exactly the silent wrong click on a live account the job skill forbids. The 255-option cap forces pruning, which is where rtrvr's cost rose 38–51%. Repetition is already routed to model-free jobs. |
| **C.** Jev inside replay (declared-branch choice, judged assertions) | **Reject** | Probabilistic behaviour where deterministic checks suffice (`Locator.or_()` plus an exactly-one assertion), not reproducible under `--seed`, and a stream of account data to a third party unattended on every run. |

What shipped is candidate A at its smallest: `failure.json` captured by the runner for
free, offline signatures first, one Jev Choice only when `--offline` is dropped, a key is
set and nothing matched, a `diagnosis.json` that stores the label and not the text, and a
first-check table that turns the label into a suspicion to confirm. Two constraints came
straight from the consultation: key presence alone is not consent to transmit page content
(the printed command is offline; the skill tells the agent to ask before dropping the
flag), and the label must never be a reason to rotate a proxy or re-baseline an identity
by reflex. A Codex review of the diff through `acpx` then tightened the redaction (titles
and links masked, redirects refused), the signatures (visible frames only, anchored
titles) and the wording (labels are suspicions, not causes).

Measured on 2026-09-22 in a headful CloakBrowser session: four real pages and six synthetic
walls labelled correctly, including a login wall carrying a hidden "you are signed in" note
(label held; the note moved 13% of the probability). Round trips were 0.8–1.0 s from
Budapest, 700–2,400 input tokens each. That is one afternoon of evidence, not a benchmark.
