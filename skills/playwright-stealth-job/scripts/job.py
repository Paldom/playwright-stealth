#!/usr/bin/env python3
"""Record a browsing job once, then replay it without an LLM in the loop.

A job is an ordinary Python module committed at `<root>/jobs/<identity>/<name>.py`. It
declares which identity it runs on, how to tell the session is still alive, and a `run()`
that drives the page. The agent authors it once; every later run is this script executing
that file — no DOM reading, no model call, no per-run cost.

    jobs/<identity>/<name>.py        the job          COMMITTED, reviewable, no secrets
    .stealth/<identity>/runs/...     run artefacts    GITIGNORED, pruned, secret-bearing

Why a module and not a step list: the durable Playwright API (`get_by_role`,
`get_by_label`) is also the humanized one — CloakBrowser patches the whole Locator class,
verified in its wrapper source — so real code gets durability and human-like input from
the same calls, and gets loops, pagination and "this month" for free. The safety a step
schema would give by construction is enforced here instead by `--lint`, which is an AST
allowlist over the house rules. Run the linter; do not trust review.

Modes:
  --new      scaffold a job file from the template
  --lint     AST-check a job file against the house rules (no browser)
  --run      replay the job on its identity
  --list     list jobs and their last run
  --self-test  linter + jitter logic only, no browser

Exit codes: 0 ok, 1 job/lint/run failure, 2 usage.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import json
import os
import random
import re
import secrets as _secrets
import shutil
import sys
import time
from pathlib import Path

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "assets", "job.py.template")
JOBS_DIR = "jobs"
RUNS_DIR = "runs"
KEEP_SUCCESSFUL_RUNS = 10
SECRET_ENV_PREFIX = "STEALTH_SECRET_"  # noqa: S105 - an environment-variable NAME, not a credential
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# --- house rules the linter enforces ----------------------------------------------
# Calls that silently strip humanization or emit a documented bot tell.
BANNED_CALLS = {
    "wait_for_timeout": "emits CDP traffic that behavioural scoring reads — use h.pause()",
    "query_selector": "returns an ElementHandle, which bypasses every humanize patch — "
    "use page.get_by_role(...) / page.locator(...)",
    "query_selector_all": "returns ElementHandles, which bypass every humanize patch",
    "set_input_files": "not humanized; drive the file chooser through the page instead",
}
# Substrings that are a bot tell whenever they appear inside evaluated JS.
BANNED_IN_JS = ("scrollto", "scrollby", ".click(", ".focus(", "dispatchevent")
# Locator chains whose typed value must never be a literal.
SECRETISH = re.compile(
    r"password|passwd|\bpwd\b|otp|one[-_ ]?time|2fa|mfa|\bpin\b|secret|token", re.I
)
TYPING_CALLS = ("fill", "type", "press_sequentially")
REQUIRED_ATTRS = ("IDENTITY", "LOGGED_IN_WHEN", "run")


class JobError(RuntimeError):
    """Something is wrong with the job itself. Message is shown to the user verbatim."""


# ------------------------------------------------------------------------- linting


def lint_source(src: str, path: str = "<job>") -> list[dict]:
    """AST-check a job against the house rules. Pure — no I/O, no import of the job.

    Returns a list of {level, line, rule, detail}. `level` is "error" or "warn".
    Importing the module to inspect it would execute it, so everything here is static.
    """
    problems: list[dict] = []

    def add(level: str, node: ast.AST, rule: str, detail: str) -> None:
        problems.append(
            {"level": level, "line": getattr(node, "lineno", 0), "rule": rule, "detail": detail}
        )

    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        return [{"level": "error", "line": exc.lineno or 0, "rule": "syntax", "detail": str(exc)}]

    # Structural contract: the runner needs these three names.
    top = {
        t.id
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    top |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for required in REQUIRED_ATTRS:
        if required not in top:
            add(
                "error",
                tree.body[0] if tree.body else tree,
                "contract",
                f"job must define {required}",
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if attr in BANNED_CALLS:
            add("error", node, attr, BANNED_CALLS[attr])

        if attr in ("evaluate", "eval_on_selector", "evaluate_handle"):
            js = " ".join(
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ).lower()
            hit = next((b for b in BANNED_IN_JS if b in js), None)
            if hit:
                add(
                    "error",
                    node,
                    "evaluate",
                    f"evaluated JS contains {hit!r} — untrusted "
                    "input is a binary bot tell; drive it through the page API",
                )
            else:
                add(
                    "warn",
                    node,
                    "evaluate",
                    "evaluate() runs in the page; fine for reading data, never for input",
                )

        # A literal typed into a secret-looking target is a credential in git.
        if attr in TYPING_CALLS and node.args:
            target = ast.get_source_segment(src, node.func) or ""
            first = node.args[0]
            if (
                SECRETISH.search(target)
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                add(
                    "error",
                    node,
                    "secret-literal",
                    f'literal typed into {target.strip()[:60]!r} — use h.secret("NAME")',
                )

        # A constant sleep makes every run identically timed across runs.
        if attr == "sleep" and node.args and isinstance(node.args[0], ast.Constant):
            add(
                "warn",
                node,
                "fixed-sleep",
                "fixed sleep — identical gaps every run are a cross-run signature; "
                "use h.pause() which jitters from the run seed",
            )

    # ast.walk is breadth-first, so findings come out in node order, not reading order.
    problems.sort(key=lambda p: (p["line"], p["rule"]))
    return problems


def lint_file(path: str) -> tuple[list[dict], bool]:
    try:
        src = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise JobError(f"cannot read {path}: {exc}") from None
    problems = lint_source(src, path)
    return problems, not any(p["level"] == "error" for p in problems)


# ------------------------------------------------------------------------- helpers


class Helpers:
    """The sealed surface a job may use for timing, secrets and assertions.

    `pause` is the reason replays are not identical across runs: it draws from the run's
    seeded RNG, so production runs vary while any past run can be reproduced exactly by
    passing its recorded seed back in.
    """

    def __init__(self, rng: random.Random, log: list[str]) -> None:
        self._rng = rng
        self.log = log

    def pause(self, low: float = 0.7, high: float = 2.5) -> None:
        """Sleep a jittered human-ish beat. Never a constant."""
        delay = self._rng.uniform(low, high)
        self.log.append(f"pause {delay:.2f}s")
        time.sleep(delay)

    def secret(self, name: str) -> str:
        """Resolve a secret by name from the environment. Never stored in the job file."""
        env = SECRET_ENV_PREFIX + name.upper().replace("-", "_")
        value = os.environ.get(env)
        if not value:
            raise JobError(
                f"secret {name!r} is not set — export {env}. Refusing to continue: typing "
                "an empty string into a credential field can lock the account out."
            )
        return value

    def expect(self, condition: bool, message: str) -> None:
        """Outcome assertion. A job that asserts nothing cannot prove it worked."""
        self.log.append(f"expect {message}: {'ok' if condition else 'FAILED'}")
        if not condition:
            raise JobError(f"assertion failed: {message}")


# ---------------------------------------------------------------------------- paths


def job_path(root: str, identity: str, job: str) -> str:
    for value, label in ((identity, "identity"), (job, "job")):
        if not NAME_RE.match(value or ""):
            raise JobError(f"invalid {label} name {value!r} — use kebab-case")
    return os.path.join(os.path.abspath(root), JOBS_DIR, identity, f"{job}.py")


def runs_root(root: str, identity: str, job: str) -> str:
    sys.path.insert(0, os.path.join(SKILL_DIR, "..", "playwright-stealth", "scripts"))
    from launch import identity_dir

    return os.path.join(identity_dir(identity, root), RUNS_DIR, job)


def prune_runs(base: str, keep: int = KEEP_SUCCESSFUL_RUNS) -> int:
    """Delete the oldest successful runs. Failed runs are never pruned — they are the
    entire debugging story, and a successful trace is stale within a day."""
    if not os.path.isdir(base):
        return 0
    successes = []
    for name in sorted(os.listdir(base)):
        manifest = os.path.join(base, name, "manifest.json")
        with contextlib.suppress(Exception):
            if json.loads(Path(manifest).read_text(encoding="utf-8")).get("ok"):
                successes.append(name)
    removed = 0
    for name in successes[:-keep] if len(successes) > keep else []:
        shutil.rmtree(os.path.join(base, name), ignore_errors=True)
        removed += 1
    return removed


# ------------------------------------------------------------------------ execution


def load_job(path: str):
    if not os.path.isfile(path):
        raise JobError(f"no job at {path} — create one with --new")
    problems, ok = lint_file(path)
    if not ok:
        for p in problems:
            if p["level"] == "error":
                print(f"LINT {path}:{p['line']} [{p['rule']}] {p['detail']}", file=sys.stderr)
        raise JobError("job failed the lint gate — fix the errors above, never the linter")
    spec = importlib.util.spec_from_file_location(f"stealth_job_{os.path.basename(path)}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for required in REQUIRED_ATTRS:
        if not hasattr(module, required):
            raise JobError(f"job {path} does not define {required}")
    return module


def run_job(identity: str, job: str, root: str, seed: int | None, keep_trace: bool) -> int:
    path = job_path(root, identity, job)
    module = load_job(path)
    if identity != module.IDENTITY:
        raise JobError(
            f"job declares IDENTITY={module.IDENTITY!r} but was run with "
            f"{identity!r} — refusing to run a job against another account"
        )

    seed = seed if seed is not None else _secrets.randbelow(2**31)
    rng = random.Random(seed)  # noqa: S311 - jitter and humanisation timing, never cryptographic
    log: list[str] = []
    helpers = Helpers(rng, log)
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]

    base = runs_root(root, identity, job)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = os.path.join(base, stamp)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)

    sys.path.insert(0, os.path.join(SKILL_DIR, "..", "playwright-stealth", "scripts"))
    from launch import open_identity

    started = time.time()
    ok, error, result = False, None, None
    print(f"STATUS job: {identity}/{job} sha={sha} seed={seed}")
    with open_identity(identity, root) as ctx:
        traced = False
        with contextlib.suppress(Exception):
            ctx.tracing.start(screenshots=True, snapshots=True)
            traced = True
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            start_url = getattr(module, "START_URL", None)
            if start_url:
                page.goto(start_url, wait_until="domcontentloaded")
            # Session first. Auth drift breaks replays far more often than selectors do,
            # and a dead session is where a blind replay does its most confusing damage.
            if page.locator(module.LOGGED_IN_WHEN).count() == 0:
                raise JobError(
                    f"session looks dead — {module.LOGGED_IN_WHEN!r} not found on "
                    f"{page.url}. Re-run the hand-login for {identity!r}, then retry. "
                    "The job was NOT run."
                )
            result = module.run(page, ctx, helpers)
            ok = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                page.screenshot(path=os.path.join(run_dir, "failure.png"), full_page=True)
            with contextlib.suppress(Exception):
                Path(os.path.join(run_dir, "failure.html")).write_text(
                    page.content()[:400_000], encoding="utf-8"
                )
            with contextlib.suppress(Exception):
                Path(os.path.join(run_dir, "url.txt")).write_text(page.url, encoding="utf-8")
        finally:
            if traced:
                with contextlib.suppress(Exception):
                    ctx.tracing.stop(path=os.path.join(run_dir, "trace.zip"))

    manifest = {
        "identity": identity,
        "job": job,
        "ok": ok,
        "seed": seed,
        "job_sha256": sha,
        "started": stamp,
        "duration_s": round(time.time() - started, 2),
        "error": error,
        "steps": log,
    }
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    if result is not None:
        with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
    # A successful trace is stale within a day; a failed one is the whole story.
    if ok and not keep_trace:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(run_dir, "trace.zip"))
    pruned = prune_runs(base)

    for line in log:
        print(f"STEP {line}")
    print(f"STATUS run: {run_dir}")
    if pruned:
        print(f"STATUS pruned: {pruned} old successful run(s)")
    if not ok:
        print(f"JOB-ERROR: {error}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    print("RESULT: ok")
    return 0


# --------------------------------------------------------------------------- create


def new_job(identity: str, job: str, root: str) -> int:
    path = job_path(root, identity, job)
    if os.path.exists(path):
        raise JobError(f"{path} already exists — edit it, or pick another name")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = Path(TEMPLATE).read_text(encoding="utf-8")
    text = text.replace("{{IDENTITY}}", identity).replace("{{JOB}}", job)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"STATUS created: {path}")
    print(
        "STATUS next: drive the identity once, replace the TODO steps with what you "
        "actually saw, then --lint and --run"
    )
    print("RESULT: ok")
    return 0


def list_jobs(root: str) -> int:
    base = os.path.join(os.path.abspath(root), JOBS_DIR)
    if not os.path.isdir(base):
        print("STATUS jobs: none")
        print("RESULT: ok")
        return 0
    for identity in sorted(os.listdir(base)):
        idir = os.path.join(base, identity)
        if not os.path.isdir(idir):
            continue
        for fname in sorted(f for f in os.listdir(idir) if f.endswith(".py")):
            job = fname[:-3]
            runs = runs_root(root, identity, job)
            last, status = "never", ""
            if os.path.isdir(runs):
                stamps = sorted(os.listdir(runs))
                if stamps:
                    last = stamps[-1]
                    with contextlib.suppress(Exception):
                        m = json.loads(
                            Path(os.path.join(runs, last, "manifest.json")).read_text(
                                encoding="utf-8"
                            )
                        )
                        status = "ok" if m.get("ok") else "FAIL"
            print(f"JOB {identity}/{job}\tlast={last}\t{status}")
    print("RESULT: ok")
    return 0


# ------------------------------------------------------------------------ self-test


def self_test() -> int:
    good = """
IDENTITY = "acme-portal"
LOGGED_IN_WHEN = 'role=link[name="Sign out"]'
def run(page, ctx, h):
    page.get_by_role("link", name="Invoices").click()
    h.pause()
    rows = page.get_by_role("row").count()
    h.expect(rows > 0, "invoice table has rows")
    return {"rows": rows}
"""
    problems = lint_source(good)
    assert not [p for p in problems if p["level"] == "error"], problems

    # Each banned call is an error, and the message says what to use instead.
    for snippet, rule in (
        ("def run(page, ctx, h):\n    page.wait_for_timeout(1000)", "wait_for_timeout"),
        ("def run(page, ctx, h):\n    page.query_selector('#a').click()", "query_selector"),
        ("def run(page, ctx, h):\n    page.query_selector_all('#a')", "query_selector_all"),
    ):
        src = 'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\n' + snippet
        errs = [p for p in lint_source(src) if p["level"] == "error"]
        assert any(p["rule"] == rule for p in errs), (rule, errs)

    # JS that scrolls or clicks is a hard error; JS that only reads is a warning.
    src = 'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\ndef run(page, ctx, h):\n    page.evaluate("window.scrollTo(0, 500)")'
    errs = [p for p in lint_source(src) if p["level"] == "error"]
    assert any(p["rule"] == "evaluate" for p in errs), errs
    src = 'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\ndef run(page, ctx, h):\n    page.evaluate("document.title")'
    res = lint_source(src)
    assert not [p for p in res if p["level"] == "error"]
    assert any(p["rule"] == "evaluate" and p["level"] == "warn" for p in res), res

    # A credential literal is an error; the same literal elsewhere is not.
    src = (
        'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\ndef run(page, ctx, h):\n'
        '    page.get_by_label("Password").fill("hunter2")'
    )
    errs = [p for p in lint_source(src) if p["level"] == "error"]
    assert any(p["rule"] == "secret-literal" for p in errs), errs
    src = (
        'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\ndef run(page, ctx, h):\n'
        '    page.get_by_label("Search").fill("invoices")'
    )
    assert not [p for p in lint_source(src) if p["level"] == "error"]
    # The secret helper is the sanctioned form and must lint clean.
    src = (
        'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\ndef run(page, ctx, h):\n'
        '    page.get_by_label("Password").fill(h.secret("ACME_PW"))'
    )
    assert not [p for p in lint_source(src) if p["level"] == "error"]

    # A constant sleep warns but does not block.
    src = (
        'IDENTITY = "x"\nLOGGED_IN_WHEN = "y"\nimport time\ndef run(page, ctx, h):\n'
        "    time.sleep(2)"
    )
    res = lint_source(src)
    assert not [p for p in res if p["level"] == "error"]
    assert any(p["rule"] == "fixed-sleep" for p in res), res

    # The structural contract is enforced.
    errs = [p for p in lint_source("x = 1") if p["level"] == "error"]
    assert {p["detail"] for p in errs} >= {f"job must define {a}" for a in REQUIRED_ATTRS}

    # Broken Python is a lint error, not a crash.
    assert lint_source("def run(:")[0]["rule"] == "syntax"

    # Jitter: seeded runs reproduce exactly, unseeded ones differ.
    a = [random.Random(7).uniform(0.7, 2.5) for _ in range(3)]  # noqa: S311 - jitter and humanisation timing, never cryptographic
    b = [random.Random(7).uniform(0.7, 2.5) for _ in range(3)]  # noqa: S311 - jitter and humanisation timing, never cryptographic
    assert a == b
    assert a != [random.Random(8).uniform(0.7, 2.5) for _ in range(3)]  # noqa: S311 - jitter and humanisation timing, never cryptographic

    # A missing secret must name the variable rather than return empty.
    h = Helpers(random.Random(1), [])  # noqa: S311 - jitter and humanisation timing, never cryptographic
    os.environ.pop("STEALTH_SECRET_NOPE", None)
    try:
        h.secret("nope")
    except JobError as exc:
        assert "STEALTH_SECRET_NOPE" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing secret should raise")
    os.environ["STEALTH_SECRET_YES"] = "v"  # noqa: S105 - an environment-variable NAME, not a credential
    try:
        assert h.secret("yes") == "v"
    finally:
        del os.environ["STEALTH_SECRET_YES"]

    # expect() raises on a false condition and records both outcomes.
    log: list[str] = []
    h2 = Helpers(random.Random(1), log)  # noqa: S311 - jitter and humanisation timing, never cryptographic
    h2.expect(True, "fine")
    try:
        h2.expect(False, "bad")
    except JobError:
        pass
    else:  # pragma: no cover
        raise AssertionError("failed expectation should raise")
    assert log == ["expect fine: ok", "expect bad: FAILED"]

    for bad in ("Bad Name", "a/b", ""):
        try:
            job_path(".", bad, "x")
        except JobError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{bad!r} should be rejected")

    assert os.path.isfile(TEMPLATE), f"missing template {TEMPLATE}"
    tmpl = Path(TEMPLATE).read_text(encoding="utf-8")
    assert "{{IDENTITY}}" in tmpl and "{{JOB}}" in tmpl
    rendered = tmpl.replace("{{IDENTITY}}", "acme").replace("{{JOB}}", "invoices")
    assert not [p for p in lint_source(rendered) if p["level"] == "error"], (
        "the shipped template must itself pass the lint gate"
    )

    print("self-test: ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--identity", help="identity the job runs on")
    ap.add_argument("--job", help="job name (kebab-case)")
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--new", action="store_true", help="scaffold a job file")
    ap.add_argument("--lint", action="store_true", help="AST-check the job, no browser")
    ap.add_argument("--run", action="store_true", help="replay the job")
    ap.add_argument("--list", action="store_true", help="list jobs and their last run")
    ap.add_argument("--seed", type=int, help="reproduce a past run's timing exactly")
    ap.add_argument(
        "--keep-trace",
        action="store_true",
        help="keep trace.zip even on success (it is secret-bearing)",
    )
    ap.add_argument("--self-test", action="store_true", help="linter + jitter logic only")
    args = ap.parse_args()

    try:
        if args.self_test:
            return self_test()
        if args.list:
            return list_jobs(args.root)
        if not (args.identity and args.job):
            ap.error("--identity and --job are required (or use --list / --self-test)")
        if args.new:
            return new_job(args.identity, args.job, args.root)
        if args.lint:
            path = job_path(args.root, args.identity, args.job)
            problems, ok = lint_file(path)
            for p in problems:
                print(f"LINT {path}:{p['line']} [{p['level'].upper()}] {p['rule']} — {p['detail']}")
            print(f"RESULT: {'ok' if ok else 'fail'}")
            return 0 if ok else 1
        if args.run:
            return run_job(args.identity, args.job, args.root, args.seed, args.keep_trace)
        ap.error("pick a mode: --new, --lint, --run, --list")
    except JobError as exc:
        print(f"JOB-ERROR: {exc}", file=sys.stderr)
        print("RESULT: fail")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
