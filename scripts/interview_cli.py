"""Text-mode interview in the terminal: create an interview, review the brief, type your answers.

It is a small client of the API: it only sends HTTP requests (the same ones a web page will
send in Phase 2) and prints the replies; all the interview logic runs in the server. It uses
only Python's standard library, so it runs on your machine without installing anything.

Flow: `new` creates the interview, adds the documents and starts a session. Then run() loops:
show what the session is waiting for (the brief or a question), read your input, POST it to
/sessions/{id}/resume, and repeat until the final feedback.

    python scripts/interview_cli.py new --company "Acme" --role "Backend Engineer" \\
        --doc job_post:samples/job_post.txt --cv samples/cv.txt
    python scripts/interview_cli.py start INTERVIEW_ID     # new session, reuses an approved brief
    python scripts/interview_cli.py resume SESSION_ID      # continue a stopped session

API_URL (default http://localhost:8000) selects the server.
"""

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

API = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
EDIT_FILE = Path("brief_edit.json")


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = exc.reason
        raise ApiError(exc.code, json.dumps(detail) if not isinstance(detail, str) else detail) from None
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach the API at {API} ({exc.reason}). Is `docker compose up` running?")


# ---------- printing ----------

def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def print_list(title: str, items: list[str]) -> None:
    print(f"\n{title}:")
    for item in items or ["(none)"]:
        print(wrap(f"- {item}"))


def print_brief(brief: dict) -> None:
    print("\n" + "=" * 30 + " INTERVIEW BRIEF " + "=" * 30)
    print("\nCompany:\n" + wrap(brief["company_summary"]))
    print_list("Role requirements", brief["role_requirements"])
    print("\nLikely format:\n" + wrap(brief["interview_format"]))
    print_list("Interviewers", [f"{i['name']} ({i['role']}) {i['notes']}".strip() for i in brief["interviewers"]])
    print_list("Gaps between CV and role", brief["cv_gaps"])
    print_list("Focus areas", brief["focus_areas"])
    print_list("Probable questions", brief["probable_questions"])
    print("=" * 77)


def print_feedback(fb: dict) -> None:
    print("\n" + "=" * 30 + " FINAL FEEDBACK " + "=" * 31)
    print(f"\nOverall: {fb['overall_score']}/5\n" + wrap(fb["summary"]))
    print("\nRubric:")
    for s in fb["rubric_scores"]:
        print(wrap(f"{s['criterion']}: {s['score']}/5 - {s['note']}"))
    print("\nPer question:")
    for q in fb["per_question"]:
        print(wrap(f"[{q['score']}/5] {q['question']}"))
        print(wrap(q["comment"], indent="      "))
    print_list("Strengths", fb["strengths"])
    print_list("Fixes for next time", fb["fixes"])
    print("=" * 77)


# ---------- input ----------

def read_answer() -> str:
    print("(type your answer; finish with an empty line)")
    lines = []
    while True:
        line = input("> ")
        if not line.strip() and lines:
            return "\n".join(lines)
        if line.strip():
            lines.append(line)


def brief_decision(brief: dict) -> dict:
    print_brief(brief)
    while True:
        choice = input("\n[a]pprove, [r]equest changes, [e]dit it yourself? ").strip().lower()
        if choice == "a":
            return {"action": "approve"}
        if choice == "r":
            feedback = input("What should change? ").strip()
            if feedback:
                return {"action": "revise", "feedback": feedback}
        if choice == "e":
            EDIT_FILE.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
            input(f"Edit {EDIT_FILE.resolve()} in your editor, save, then press Enter...")
            try:
                return {"action": "edit", "brief": json.loads(EDIT_FILE.read_text(encoding="utf-8"))}
            except json.JSONDecodeError as exc:
                print(f"That file is not valid JSON ({exc}); try again.")


# ---------- session loop ----------

def run(view: dict) -> None:
    print(f"\nSession {view['session_id']}")
    while True:
        if view["status"] == "done":
            print_feedback(view["final_feedback"])
            return
        if view["status"] == "stopped":
            if input("\nThe session stopped (a step failed). Retry now? [y/n] ").strip().lower() != "y":
                print(f"Resume later with: python scripts/interview_cli.py resume {view['session_id']}")
                return
            view = call("POST", f"/sessions/{view['session_id']}/retry")
            continue

        pending = view["pending"]
        if pending["type"] == "review_brief":
            value = brief_decision(pending["brief"])
            print("\nWorking..." if value["action"] != "revise" else "\nRewriting the brief...")
        else:
            tag = "follow-up" if pending["followup"] else f"question {pending['q_index'] + 1}/{pending['total']}"
            print(f"\nInterviewer ({tag}):\n" + wrap(pending["question"]))
            value = read_answer()

        try:
            view = call("POST", f"/sessions/{view['session_id']}/resume", {"value": value})
        except ApiError as exc:
            print(f"\n{exc}")
            view = call("GET", f"/sessions/{view['session_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="create an interview, add documents, start a session")
    new.add_argument("--company", required=True)
    new.add_argument("--role", required=True)
    new.add_argument("--mode", default="behavioral", choices=["behavioral", "job_post"])
    new.add_argument("--interviewer", action="append", default=[], help="name, repeatable")
    new.add_argument("--doc", action="append", default=[], metavar="TYPE:PATH",
                     help="job_post | recruiter_email | notes | other, then a text file; repeatable")
    new.add_argument("--cv", metavar="PATH", help="store a CV (shared by all interviews)")
    new.add_argument("--questions", type=int)

    start = sub.add_parser("start", help="new session for an existing interview")
    start.add_argument("interview_id")
    start.add_argument("--questions", type=int)

    resume = sub.add_parser("resume", help="continue an existing session")
    resume.add_argument("session_id")

    args = parser.parse_args()
    try:
        if args.cmd == "new":
            interview = call("POST", "/interviews", {
                "company": args.company, "role": args.role, "mode": args.mode, "interviewers": args.interviewer,
            })
            print(f"Interview {interview['id']} created.")
            if args.cv:
                call("POST", "/documents", {"doc_type": "cv", "source": args.cv,
                                            "text": Path(args.cv).read_text(encoding="utf-8")})
            for spec in args.doc:
                doc_type, _, path = spec.partition(":")
                call("POST", "/documents", {"interview_id": interview["id"], "doc_type": doc_type,
                                            "source": path, "text": Path(path).read_text(encoding="utf-8")})
            print("Reading the documents and writing the brief (this can take a minute)...")
            view = call("POST", f"/interviews/{interview['id']}/sessions", {"num_questions": args.questions})
        elif args.cmd == "start":
            print("Starting a session...")
            view = call("POST", f"/interviews/{args.interview_id}/sessions", {"num_questions": args.questions})
        else:
            view = call("GET", f"/sessions/{args.session_id}")
        run(view)
    except ApiError as exc:
        sys.exit(str(exc))
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Progress is saved; continue with `resume SESSION_ID`.")


if __name__ == "__main__":
    main()
