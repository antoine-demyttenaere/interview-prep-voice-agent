"""Runs the "answer scoring + follow-ups" evaluation: the real evaluate_answer node on fixed cases.

What it measures, per case (cases.yaml, plus evals/private/answer_scoring.yaml if present):
- score_in_range  1 if the overall score is inside the expected range, else 0 (headline)
- off_by          how far outside the range the score is (0 when inside)
- follow_up_ok    1 if the follow-up decision matches the expected one, else 0 (skipped when
                  either is fine). The decision checked is the node's final one, after the
                  code applies the follow-up limit.
- latency_s, cost_usd   time and price of the Claude call(s)
- invalid         1 if the case produced no usable output (Claude error, timeout); such cases
                  are counted apart, never as a wrong score
Plus, for the whole run: follow-up precision, recall and specificity.

How it runs: the cases are uploaded once as a LangSmith dataset (its name includes a hash of
the cases, so changing a case creates a new dataset version), then LangSmith's evaluate() calls
the node on each case and records an experiment you can compare with earlier ones in the
LangSmith interface. A local summary is also written to evals/answer_scoring/runs/ and compared
with the previous run.

    docker compose exec api python -m evals.answer_scoring.run                  # all cases
    docker compose exec api python -m evals.answer_scoring.run --reps 2
    docker compose exec api python -m evals.answer_scoring.run --ids strong-incident,no-answer-skip
    docker compose exec api python -m evals.answer_scoring.run --no-private     # fictional cases only
"""

import argparse
import concurrent.futures
import contextvars
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from langsmith import Client

from app.config import get_settings
from app.graph import nodes
from app.llm import LLMError, record_calls

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRIVATE = ROOT / "evals" / "private" / "answer_scoring.yaml"
RUNS = HERE / "runs"
CASE_TIMEOUT_S = 120  # hard ceiling per case; a slower case is recorded as a timeout, not a score

# $ per million tokens, from the Claude API price table (Haiku 5.5, prompts under 100K tokens).
PRICES = {"claude-haiku-5-5": {"in": 0.10, "out": 0.50}}

# Files whose content decides the result: their hash is stored with each experiment, so you can
# see in LangSmith which prompt version a run used.
HARNESS_FILES = [
    "prompts/evaluate_answer.md", "prompts/interviewer_system.md", "rubrics/behavioral.yaml",
    "app/graph/nodes.py", "app/graph/schemas.py", "app/llm.py",
]


# ---------- cases ----------

def load_cases(with_private: bool) -> list[dict]:
    public = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    fixture = json.loads((HERE / "brief.json").read_text(encoding="utf-8"))
    cases = []
    for c in public["cases"]:
        cases.append({
            "id": c["id"], "tags": c["tags"], "why": c["why"], "private": False,
            "mode": c.get("mode", public["mode"]), "language": c.get("language", public["language"]),
            "company": fixture["company"], "role": fixture["role"], "brief": fixture["brief"],
            "planned_question": c["planned_question"], "exchange": c["exchange"],
            "followups_used": c.get("followups_used", 0), "expected": c["expected"],
        })
    if with_private and PRIVATE.exists():
        for c in yaml.safe_load(PRIVATE.read_text(encoding="utf-8"))["cases"]:
            cases.append({**c, "private": True})
    return cases


def dataset_name(cases: list[dict]) -> str:
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
    return f"interview-answer-scoring-{digest}"


def ensure_dataset(client: Client, cases: list[dict]) -> str:
    """Upload the cases as a LangSmith dataset, once per version of the cases."""
    name = dataset_name(cases)
    if not client.has_dataset(dataset_name=name):
        ds = client.create_dataset(name, description="Answer scoring + follow-ups (evaluate_answer node)")
        client.create_examples(
            dataset_id=ds.id,
            examples=[{
                "inputs": {k: c[k] for k in ("id", "mode", "language", "company", "role", "brief",
                                             "planned_question", "exchange", "followups_used")},
                "outputs": {"overall_min": c["expected"]["overall"][0], "overall_max": c["expected"]["overall"][1],
                            "follow_up": c["expected"]["follow_up"]},
                "metadata": {"tags": c["tags"], "why": c["why"], "private": c["private"]},
            } for c in cases],
        )
    return name


# ---------- running the node on one case ----------

def build_state(inputs: dict) -> dict:
    """The graph state evaluate_answer would see at this moment of the interview."""
    transcript, interviewer_turns = [], 0
    for turn in inputs["exchange"]:
        role, text = next(iter(turn.items()))
        if role == "interviewer":
            interviewer_turns += 1
        transcript.append({"role": role, "text": text, "q_index": 0, "followup": interviewer_turns > 1})
    last_question = [t["text"] for t in transcript if t["role"] == "interviewer"][-1]
    return {
        "mode": inputs["mode"], "language": inputs["language"],
        "company": inputs["company"], "role": inputs["role"], "brief": inputs["brief"],
        "questions": [{"text": inputs["planned_question"], "topic": "", "rationale": "", "based_on": ""}],
        "q_index": 0, "followups": inputs["followups_used"],
        "followup_focus": "follow-up" if inputs["followups_used"] else None,
        "current_question": last_question, "transcript": transcript, "evaluations": [],
    }


def cost_usd(calls: list[dict]) -> float:
    total = 0.0
    for c in calls:
        price = next((p for m, p in PRICES.items() if c["model"].startswith(m)), None)
        if price is None:
            raise RuntimeError(f"No price for model {c['model']}: add it to PRICES")
        u = c["usage"]
        tokens_in = u["input_tokens"] + 1.25 * u["cache_creation_input_tokens"] + 0.1 * u["cache_read_input_tokens"]
        total += (tokens_in * price["in"] + u["output_tokens"] * price["out"]) / 1_000_000
    return round(total, 6)


def target(inputs: dict) -> dict:
    """Run evaluate_answer on one case. Never raises: problems come back as status=error."""
    expected_model = get_settings().model_turn

    def work():
        with record_calls() as calls:
            update = nodes.evaluate_answer(build_state(inputs))
        return update, calls

    started = time.perf_counter()
    # Run in a worker thread so a hung call can be abandoned after CASE_TIMEOUT_S; copy_context
    # keeps the LangSmith trace and the call recording attached to this case.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(contextvars.copy_context().run, work)
    try:
        update, calls = future.result(timeout=CASE_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        return {"status": "error", "error_class": "timeout", "error": f"over {CASE_TIMEOUT_S}s"}
    except LLMError as exc:
        return {"status": "error", "error_class": "claude", "error": str(exc)}
    except Exception as exc:  # a bug in the harness, not a model result
        return {"status": "error", "error_class": "harness", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        pool.shutdown(wait=False)

    served = {c["model"] for c in calls}
    if not served or any(not m.startswith(expected_model) for m in served):
        return {"status": "error", "error_class": "model_mismatch", "error": f"asked {expected_model}, got {served}"}
    ev = update["evaluations"][-1]
    return {
        "status": "ok",
        "overall": ev["overall"],
        "follow_up": bool(update.get("followup_focus")),  # the node's final decision (after the cap)
        "claude_follow_up": ev["follow_up"],               # what Claude asked for, before the cap
        "follow_up_focus": ev["follow_up_focus"],
        "summary": ev["summary"],
        "scores": ev["scores"],
        "latency_s": round(time.perf_counter() - started, 3),
        "cost_usd": cost_usd(calls),
        "model": sorted(served)[0],
        "usage": calls[0]["usage"],
    }


# ---------- graders (LangSmith evaluators) ----------

def _ok(outputs) -> bool:
    return bool(outputs) and outputs.get("status") == "ok"


def score_in_range(outputs: dict, reference_outputs: dict) -> dict:
    if not _ok(outputs):
        return {"key": "score_in_range", "score": None}
    inside = reference_outputs["overall_min"] <= outputs["overall"] <= reference_outputs["overall_max"]
    return {"key": "score_in_range", "score": int(inside),
            "comment": f"got {outputs['overall']}, expected {reference_outputs['overall_min']}-{reference_outputs['overall_max']}"}


def off_by(outputs: dict, reference_outputs: dict) -> dict:
    if not _ok(outputs):
        return {"key": "off_by", "score": None}
    o, lo, hi = outputs["overall"], reference_outputs["overall_min"], reference_outputs["overall_max"]
    return {"key": "off_by", "score": max(0, lo - o, o - hi)}


def follow_up_ok(outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs.get("follow_up")
    if not _ok(outputs) or expected is None:
        return {"key": "follow_up_ok", "score": None}
    return {"key": "follow_up_ok", "score": int(outputs["follow_up"] == expected),
            "comment": f"followed up: {outputs['follow_up']}, expected: {expected}"}


def latency_s(outputs: dict) -> dict:
    return {"key": "latency_s", "score": outputs.get("latency_s") if _ok(outputs) else None}


def cost(outputs: dict) -> dict:
    return {"key": "cost_usd", "score": outputs.get("cost_usd") if _ok(outputs) else None}


def invalid(outputs: dict) -> dict:
    return {"key": "invalid", "score": 0 if _ok(outputs) else 1,
            "comment": None if _ok(outputs) else f"{outputs.get('error_class')}: {outputs.get('error')}"}


def follow_up_confusion(outputs: list[dict], reference_outputs: list[dict]) -> list[dict]:
    """Precision / recall / specificity of the follow-up decision, over the cases with an expectation."""
    tp = fp = tn = fn = 0
    for out, ref in zip(outputs, reference_outputs):
        if not _ok(out) or ref.get("follow_up") is None:
            continue
        got, want = out["follow_up"], ref["follow_up"]
        tp += got and want
        fp += got and not want
        tn += (not got) and (not want)
        fn += (not got) and want
    div = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    return [
        {"key": "follow_up_precision", "score": div(tp, tp + fp)},
        {"key": "follow_up_recall", "score": div(tp, tp + fn)},
        {"key": "follow_up_specificity", "score": div(tn, tn + fp)},
    ]


# ---------- local summary ----------

def mean_ci(values: list[int]) -> tuple[float | None, list[float] | None]:
    """Pass rate and its 95% Wilson interval [low, high]. Unlike mean +/- 2 standard errors, the
    Wilson interval stays honest with few cases or when every case passes (it never says +/- 0)."""
    if not values:
        return None, None
    n, p, z = len(values), statistics.fmean(values), 1.96
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(p, 3), [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["outputs"].get("status") == "ok"]
    in_range = [r["grades"]["score_in_range"] for r in ok]
    fu = [r["grades"]["follow_up_ok"] for r in ok if r["grades"]["follow_up_ok"] is not None]
    lat = sorted(r["outputs"]["latency_s"] for r in ok)
    m, ci = mean_ci(in_range)
    by_tag: dict[str, list[int]] = {}
    for r in ok:
        by_tag.setdefault(r["tags"][0], []).append(r["grades"]["score_in_range"])
    return {
        "cases": len(rows), "ok": len(ok), "invalid": len(rows) - len(ok),
        "score_in_range": m, "score_in_range_ci95": ci,
        "follow_up_ok": round(statistics.fmean(fu), 3) if fu else None,
        "mean_off_by": round(statistics.fmean(r["grades"]["off_by"] for r in ok), 3) if ok else None,
        "latency_s_median": round(statistics.median(lat), 2) if lat else None,
        "latency_s_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else None,
        "cost_usd_total": round(sum(r["outputs"]["cost_usd"] for r in ok), 5),
        "score_in_range_by_tag": {t: round(statistics.fmean(v), 2) for t, v in sorted(by_tag.items())},
    }


def harness_hash() -> str:
    h = hashlib.sha256()
    for f in HARNESS_FILES:
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:10]


def git_commit() -> str:
    head = (ROOT / ".git" / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        ref = ROOT / ".git" / head[5:]
        return ref.read_text().strip()[:7] if ref.exists() else "unknown"
    return head[:7]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", help="comma-separated case ids (e.g. a pilot)")
    parser.add_argument("--reps", type=int, default=1, help="repetitions per case (more = tighter interval)")
    parser.add_argument("--no-private", action="store_true", help="leave out the cases from your real answers")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    all_cases = load_cases(with_private=not args.no_private)
    client = Client()
    name = ensure_dataset(client, all_cases)
    examples = list(client.list_examples(dataset_name=name))
    if args.ids:
        wanted = set(args.ids.split(","))
        examples = [e for e in examples if e.inputs["id"] in wanted]
        missing = wanted - {e.inputs["id"] for e in examples}
        if missing:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(missing))}")

    settings = get_settings()
    harness = harness_hash()
    print(f"{len(examples)} cases x {args.reps} rep(s) on {settings.model_turn} | dataset {name} | prompts {harness}")
    results = client.evaluate(
        target,
        data=examples,
        evaluators=[score_in_range, off_by, follow_up_ok, latency_s, cost, invalid],
        summary_evaluators=[follow_up_confusion],
        experiment_prefix=f"answer-scoring-{harness}",
        description="evaluate_answer: score in range + follow-up decision",
        metadata={"model": settings.model_turn, "prompt_hash": harness, "commit": git_commit(),
                  "pilot": bool(args.ids), "reps": args.reps},
        num_repetitions=args.reps,
        max_concurrency=args.concurrency,
    )

    rows = []
    for r in results:
        grades = {e.key: e.score for e in r["evaluation_results"]["results"]}
        rows.append({"id": r["example"].inputs["id"], "tags": r["example"].metadata["tags"],
                     "expected": r["example"].outputs, "outputs": r["run"].outputs or {}, "grades": grades})
    summary = summarize(rows)
    summary.update({"experiment": results.experiment_name, "dataset": name, "prompt_hash": harness,
                    "model": settings.model_turn, "reps": args.reps, "pilot": bool(args.ids),
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # Per-case lines, then the summary and the change since the previous comparable run.
    print()
    for r in sorted(rows, key=lambda r: r["id"]):
        o, g, e = r["outputs"], r["grades"], r["expected"]
        if o.get("status") != "ok":
            print(f"  ERROR  {r['id']:28} {o.get('error_class')}: {o.get('error')}")
            continue
        fu = "" if g["follow_up_ok"] is None else ("follow-up ok" if g["follow_up_ok"] else "FOLLOW-UP WRONG")
        mark = "ok   " if g["score_in_range"] else "WRONG"
        print(f"  {mark}  {r['id']:28} score {o['overall']} (expected {e['overall_min']}-{e['overall_max']})"
              f"  followed up: {str(o['follow_up']):5}  {fu}  {o['latency_s']:.1f}s")
    print("\nSummary:", json.dumps({k: v for k, v in summary.items() if k != "score_in_range_by_tag"}, indent=1))
    print("By kind of answer:", summary["score_in_range_by_tag"])

    RUNS.mkdir(exist_ok=True)
    previous = sorted(p for p in RUNS.glob("*.json")
                      if json.loads(p.read_text())["summary"].get("pilot") == summary["pilot"])
    out = RUNS / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False, default=str))
    if previous:
        before = json.loads(previous[-1].read_text())["summary"]
        print(f"\nCompared with {previous[-1].name} (prompts {before['prompt_hash']}):")
        for key in ("score_in_range", "follow_up_ok", "mean_off_by", "latency_s_median", "cost_usd_total"):
            a, b = before.get(key), summary.get(key)
            if a is not None and b is not None:
                print(f"  {key:18} {a} -> {b}  ({b - a:+.3f})")
    print(f"\nLangSmith experiment: {results.experiment_name} (Datasets & Experiments > {name})")


if __name__ == "__main__":
    main()
