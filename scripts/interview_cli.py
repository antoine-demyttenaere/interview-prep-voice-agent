"""Text-mode interview in the terminal: create an interview, review the brief, type your answers.

It is a small client of the API: it only sends HTTP requests (the same ones the web page sends)
and prints the replies; all the interview logic runs in the server. It uses only Python's
standard library, so it runs on your machine without installing anything.

Flow: `new` creates the interview, uploads the documents (PDF, DOCX, EML or text files; the
server reads, chunks and embeds them) and starts a session. Then run() loops:
show what the session is waiting for (the brief or a question), read your input, POST it to
/sessions/{id}/resume, and repeat until the final feedback.

    python scripts/interview_cli.py new --company "Acme" --role "Backend Engineer" \\
        --doc job_post:samples/job_post.txt --cv samples/cv.txt
    python scripts/interview_cli.py start INTERVIEW_ID     # new session, reuses an approved brief
    python scripts/interview_cli.py resume SESSION_ID      # continue a stopped session

API_URL (default http://localhost:8000) selects the server.

Logging in: there are no passwords. The first time, the script asks for your email (or reads
INTERVIEW_EMAIL), the server emails you a 6-digit code (locally it is printed in
`docker compose logs api`), and you type it here. The server answers with a login cookie,
which the script sends back with every request (the `Cookie` header), like a browser does, and
saves in ~/.interview_cli_login (your home folder, not the project), so the next runs don't
ask again until it expires (30 days) or you log out on the server. Delete that file to log out.
An admin with a password (Phase 4.8) can set INTERVIEW_EMAIL and INTERVIEW_PASSWORD instead:
the script then logs in with the password and no code is needed.
A new email also creates the account, as on the web page.
"""

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
EDIT_FILE = Path("brief_edit.json")
COOKIE = {}  # {"Cookie": "login=..."} once logged in; added to every request
LOGIN_FILE = Path.home() / ".interview_cli_login"  # the saved cookie, private to your account


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **COOKIE})
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


def _post_json(path: str, body: dict):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        sys.exit(f"Login failed: {json.loads(exc.read()).get('detail', exc.reason)}")
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach the API at {API} ({exc.reason}). Is `docker compose up` running?")


def login() -> None:
    """Reuse the saved login if it still works; otherwise log in with a code sent by email."""
    if LOGIN_FILE.exists():
        COOKIE["Cookie"] = LOGIN_FILE.read_text(encoding="utf-8").strip()
        try:
            me = call("GET", "/auth/me")
            print(f"Logged in as {me['email']}.")
            return
        except ApiError:
            COOKIE.clear()  # expired or logged out: ask for a new code

    email = os.getenv("INTERVIEW_EMAIL") or input("Email: ")
    password = os.getenv("INTERVIEW_PASSWORD")
    if password:  # admins only
        resp = _post_json("/auth/password-login", {"email": email, "password": password})
    else:
        print(json.loads(_post_json("/auth/start", {"email": email}).read())["detail"])
        code = input("Code from the email: ")
        resp = _post_json("/auth/verify", {"email": email, "code": code})
    with resp:
        for header in resp.headers.get_all("Set-Cookie") or []:
            if header.startswith("login="):
                COOKIE["Cookie"] = header.split(";", 1)[0]  # "login=<token>", without its options
    if not COOKIE:
        sys.exit("Login failed: the server sent no login cookie.")
    LOGIN_FILE.write_text(COOKIE["Cookie"], encoding="utf-8")
    try:
        LOGIN_FILE.chmod(0o600)  # readable by you only (ignored on Windows, where home folders are private)
    except OSError:
        pass
    print(f"Logged in as {email.strip().lower()}.")


def upload(path: str, fields: dict) -> dict:
    """POST a file to /documents/upload as a multipart form (what a browser's file input sends)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    filename = Path(path).name
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + Path(path).read_bytes()
        + b"\r\n"
    )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        API + "/documents/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **COOKIE},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, json.loads(exc.read()).get("detail", exc.reason)) from None


def report(doc: dict) -> None:
    note = f" (not indexed: {doc['embedding_error']})" if doc.get("embedding_error") else ""
    print(f"  added {doc['doc_type']}: {doc['source']}, {doc['chars']} characters, {doc['chunks']} chunks{note}")


# ---------- printing ----------

def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def print_list(title: str, items: list[str]) -> None:
    print(f"\n{title}:")
    for item in items or ["(none)"]:
        print(wrap(f"- {item}"))


def print_brief(brief: dict) -> None:
    print("\n" + "=" * 30 + " INTERVIEW BRIEF " + "=" * 30)
    print_list("Focus on", brief["focus_areas"])
    print("\nThe company:\n" + wrap(brief["company_summary"]))
    print("\nThe interview:\n" + wrap(brief["interview_format"]))
    if brief["interviewers"]:
        print_list("Interviewers", [f"{i['name']}, {i['role']} {i['notes']}".strip() for i in brief["interviewers"]])
    print_list("The role needs", brief["role_requirements"])
    print_list("Your gaps", brief["cv_gaps"])
    print_list("Likely questions", brief["probable_questions"])
    print("=" * 77)


def print_feedback(fb: dict) -> None:
    print("\n" + "=" * 30 + " FINAL FEEDBACK " + "=" * 31)
    print(f"\nOverall: {fb['overall_score']}/5\n" + wrap(fb["summary"]))
    print("\nRubric:")
    for s in fb["rubric_scores"]:
        print(wrap(f"{s['criterion']}: {s['score']}/5 - {s['note']}"))
        for idea in s.get("improve", []):
            print(wrap(f"-> {idea}", indent="      "))
    print("\nPer question:")
    for q in fb["per_question"]:
        print(wrap(f"[{q['score']}/5] {q['question']}"))
        print(wrap(q["comment"], indent="      "))
    print_list("Strengths", fb["strengths"])
    print_list("Fixes for next time", fb["fixes"])
    print("=" * 77)


# ---------- input ----------

def read_answer() -> str:
    print("(type your answer; finish with an empty line. Type /end to stop and get your feedback)")
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
            if value.strip() == "/end":
                print("\nWriting your feedback...")
                try:
                    view = call("POST", f"/sessions/{view['session_id']}/end")
                except ApiError as exc:
                    print(f"\n{exc}")
                    view = call("GET", f"/sessions/{view['session_id']}")
                continue

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
    new.add_argument("--mode", default="behavioral",
                     choices=["behavioral", "job_post", "technical_backend", "system_design"])
    new.add_argument("--language", default="en", choices=["en", "fr", "es"],
                     help="language of the interview, brief and feedback")
    new.add_argument("--interviewer", action="append", default=[], help="name, repeatable")
    new.add_argument("--doc", action="append", default=[], metavar="TYPE:PATH",
                     help="job_post | recruiter_email | notes | other, then a PDF, DOCX, EML or text file; repeatable")
    new.add_argument("--cv", metavar="PATH", help="store a CV file (shared by all interviews)")
    new.add_argument("--questions", type=int)

    start = sub.add_parser("start", help="new session for an existing interview")
    start.add_argument("interview_id")
    start.add_argument("--questions", type=int)

    resume = sub.add_parser("resume", help="continue an existing session")
    resume.add_argument("session_id")

    args = parser.parse_args()
    login()
    try:
        if args.cmd == "new":
            interview = call("POST", "/interviews", {
                "company": args.company, "role": args.role, "mode": args.mode, "language": args.language,
                "interviewers": args.interviewer,
            })
            print(f"Interview {interview['id']} created.")
            if args.cv:
                report(upload(args.cv, {"doc_type": "cv"}))
            for spec in args.doc:
                doc_type, _, path = spec.partition(":")
                report(upload(path, {"interview_id": interview["id"], "doc_type": doc_type}))
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
