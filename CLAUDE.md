# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See [plan.md](plan.md) for the goal, stack, phases and repo layout, and [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for how the code works (section 13 lists the day-to-day commands, section 14 the steps for common changes).

## Commands

The app runs in Docker Compose (Windows host, PowerShell). The project folder is mounted into the `api` container, and uvicorn reloads on changes in `app/` and `prompts/`, so code changes need no rebuild.

- Start: `docker compose up -d` (applies migrations and seeds the question bank, then serves http://localhost:8000, API docs at `/docs`)
- Logs: `docker compose logs -f api`
- All tests: `docker compose exec api pytest -q`
- One test: `docker compose exec api pytest -q tests/test_graph_flow.py::test_end_endpoint`
- New migration: `docker compose exec api alembic revision --autogenerate -m "add X"`, review the file, then `docker compose restart api`
- Create an account (needed to use the web page and the CLI): `docker compose exec api python scripts/create_user.py --email you@example.com [--admin]` (asks for the password, or reads `NEW_USER_PASSWORD`)
- Real Claude + LangSmith check: `docker compose exec api python scripts/smoke_claude.py`
- Answer-scoring eval (real Claude, costs money, ~2 min): `./scripts/eval.ps1` (defaults to `--reps 2`). A quick pilot: `./scripts/eval.ps1 --reps 1 --ids strong-incident,no-answer-skip`. Add `--no-private` to skip the private cases. Run it after changing a prompt, rubric or model.
- Typed interview in a terminal: `python scripts/interview_cli.py new|start|resume ...`, run on the host, not in the container (standard library only, talks to the API over HTTP; logs in with `INTERVIEW_EMAIL` / `INTERVIEW_PASSWORD` or asks; see HOW_IT_WORKS.md section 13)
- After editing `question_bank/*.yaml`: `docker compose exec api python scripts/seed_question_bank.py` (syncs the table and embeds only new questions; it also runs on every container start)
- Re-embed documents: `docker compose exec api python scripts/embed_documents.py` (only documents without chunks), add `--all` after changing the chunk size or embedding model
- After editing `requirements.txt` or the Dockerfile: `docker compose up -d --build`. After editing `.env`: `docker compose up -d --force-recreate api`.

There is no linter or formatter config. Postgres is on host port **5433** (`app` / `app`, database `interview`).

Tests need the `db` container running. They use fakes for Claude, Voyage, Deepgram and ElevenLabs ([tests/conftest.py](tests/conftest.py)), so they make no API calls. They run against the real prompts, graph, API and Postgres checkpointer, in a separate `interview_test` database that is created and migrated automatically. The fake Claude asks for a follow-up when an answer contains "vague". When you add a node or a structured-output schema, extend the fake. Every test runs logged in as `tests@example.com` (the autouse `logged_in` fixture overrides `current_user`); a test that needs the real cookie check takes the `real_auth` fixture, as in [tests/test_auth.py](tests/test_auth.py).

## Architecture in brief

- **One LangGraph graph for every interview mode** ([app/graph/build.py](app/graph/build.py), nodes in [nodes.py](app/graph/nodes.py)): `prepare_brief → review_brief (interrupt) → plan_questions → ask → answer (interrupt) → evaluate_answer → ask… → final_feedback`. Modes differ only in `prompts/modes/<mode>.md`, `rubrics/<mode>.yaml` and, for the technical modes, `question_bank/<mode>.yaml`. Nodes return only the state keys they changed ([state.py](app/graph/state.py)).
- **HTTP drives the graph** ([app/api/sessions.py](app/api/sessions.py)): each request runs the graph until the next `interrupt()` and returns a "view" (status, pending, final_feedback). Replies resume it with `Command(resume=...)`. `/turn` wraps the same step with speech-to-text before and text-to-speech after, as one LangSmith trace (`voice_turn`).
- **State lives in the Postgres checkpointer** under `thread_id` = session id. Answers and scores are stored only in the checkpoints. Our own tables ([app/db/models.py](app/db/models.py)) hold interviews, documents, chunks, question_bank and sessions. [app/main.py](app/main.py) compiles the graph with the checkpointer. The module-level `graph` in build.py has none, because LangGraph Studio brings its own.
- **All Claude calls go through [app/llm.py](app/llm.py)**: `structured()` returns Pydantic objects ([schemas.py](app/graph/schemas.py)), `text_reply()` returns plain text. Failures raise `LLMError`, which the API turns into a 502, and `/retry` re-runs the failed step. Haiku handles each turn and Sonnet handles the brief, the plan and the feedback (set in `.env` with `MODEL_TURN` and `MODEL_REASONING`).
- **Prompts are files** in `prompts/`, re-read on every call and filled with `prompts.render(name, **values)` from `$placeholders`. A missing value raises an error. The header comment is stripped before the prompt is sent. `interviewer_system.md` (which includes the brief) is prompt-cached, so anything that changes per turn (retrieved `$context`, `$bank_questions`) must go in the user prompt, never in the system prompt.
- **RAG** ([app/rag/](app/rag/)): parse (PDF, DOCX, .eml, text, web page) → chunk → embed with Voyage (`vector(1024)`) → pgvector cosine search, scoped by `interview_id`. A failed search logs a warning and the question is asked without passages, so the interview doesn't stop.
- **Evals are separate from tests** ([evals/answer_scoring/](evals/answer_scoring/)): tests check that the code works with fakes, while evals check how good Claude's decisions are. `run.py` calls the real `evaluate_answer` node on the labelled cases in `cases.yaml` (plus `evals/private/answer_scoring.yaml`) and grades them in code (`score_in_range`, `off_by`, `follow_up_ok`). Claude errors count as `invalid`, never as wrong scores. Each run is a LangSmith experiment on a dataset whose name includes a hash of the cases, and a local summary goes to `runs/`, compared with the previous run. Token and cost tracking uses `app.llm.record_calls()`.
- **Accounts** ([app/auth/](app/auth/), Phase 4.6): [main.py](app/main.py) mounts the interview and session routers with `dependencies=[Depends(current_user)]`, so they answer 401 without a valid `login` cookie; `/health`, `/auth/*` and the static page are public. Passwords are Argon2 hashes; the cookie holds a random token and only its SHA-256 hash is stored in `login_sessions` (logout deletes the row). A route that needs the user declares `user: User = Depends(current_user)`. Per-user data ownership and invite-code registration are the next steps in plan.md.
- **Speech providers sit behind interfaces** ([app/voice/stt.py](app/voice/stt.py), [tts.py](app/voice/tts.py)), selected with `STT_PROVIDER` and `TTS_PROVIDER`. Every setting comes from [app/config.py](app/config.py) through `get_settings()`.

# Project rules

## Audience

The code and docs are written so that beginners and intermediate developers can understand everything that has been built. Explain the why, define terms the first time they appear (checkpointer, migration, interrupt...), and prefer plain words.

## File headers

Every file starts with a description, in the file's own comment syntax, that lets a beginner understand the file without reading the code:

1. What the file is and what it does (first line, starting with the file's role).
2. Where it fits: what uses it, and what it uses (files, nodes, routes, services).
3. Key ideas a beginner needs to follow it, explained briefly (e.g. "a migration is...").
4. How to run, use or change it, when that applies (commands, which file to edit next).

Usually 3-15 lines; a tiny file needs less. Comment syntax:

- Python: a module docstring (`"""..."""`).
- YAML, `.ini`, Dockerfile, `.env*`, `.gitignore`, `.dockerignore`, `requirements.txt`, PowerShell: `#` comments.
- Mako templates: `##` comments (stripped from the output).
- Prompt files (`prompts/*.md`): a `<!-- ... -->` block; the loader strips it before sending to Claude, so it can be detailed. Say which node uses it, as system or user prompt, the answer shape and the placeholders.
- HTML: a `<!-- ... -->` block (before `<!doctype>`); CSS and JavaScript: a `/* ... */` block.
- Other Markdown: a title and a purpose line are enough.
- JSON and other formats without comments (e.g. `langgraph.json`): describe the file in HOW_IT_WORKS.md instead.
- Sample documents (`samples/`): one first line saying they are fictional (the text is sent to Claude).

Keep the header accurate whenever the file changes.

## Design palette

The web page uses seven colors, defined once as CSS variables at the top of [web/style.css](web/style.css). Every color on the page is one of them, a tint of one, or a gradient between them. Never add a new hue, and never use plain grey surfaces.

| Name | Hex | Role | Used for |
| --- | --- | --- | --- |
| Midnight | `#1E2350` | neutral dark | text, headings |
| Cloud | `#F5F6FB` | neutral light | page background |
| Indigo | `#4F46E5` | main color | main buttons, focus rings, current step, links, highlighted cards, score 3 |
| Coral | `#FF6B5B` | accent | recording button, "your gaps", errors, scores 1-2 |
| Teal | `#14B8A6` | accent | "End and get feedback", strengths, success, scores 4-5 |
| Sky | `#0EA5E9` | third accent | the first card of each setup step (very light Sky tint `#F4FAFE`, thin softened Sky top stripe) |
| Sun | `#F5A524` | second accent | the second card of each setup step (very light Sun tint `#FFFAEF`, thin softened Sun top stripe), small highlights |

- Cards and form fields are white (light mode); borders and soft fills are indigo tints, not grey.
- Each setup step is two cards side by side: the first in the Sky tint, the second in the Sun tint, so both halves stand out from the indigo page. Fields inside them stay white.
- Dropdowns (`select`) show their arrow as an indigo circle with a white chevron, set in from the right edge.
- The page background is one solid color, a colorful indigo tint (`#E6E4FC`, dark mode `#181C40`), and the title "Interview Practice" is solid Indigo: no gradients on either.
- Gradients are allowed only on buttons, and stay subtle: main buttons go from Indigo to a lighter indigo, the success button from Teal to a deeper teal.
- A button that must wait for the user (e.g. "Approve" before the last brief card is seen) is shown locked in a soft indigo tint, then switches to its action color (Teal for "Approve").
- Dark mode uses the same seven hues, adjusted for contrast (dark midnight background, lighter indigo / coral / teal).
- Score colors are the same everywhere: 1-2 coral, 3 indigo, 4-5 teal.

### Typography

Two fonts, loaded from Google Fonts in [web/index.html](web/index.html) and set as `--font-heading` / `--font-body` in [web/style.css](web/style.css):

- **Sora** (500-700) for headings, buttons, step numbers and scores: geometric, corporate with a bit of character.
- **DM Sans** (400-700) for body text, labels, hints and form fields: clean and readable.

Don't add a third font. The system font is only the fallback when the fonts can't load.

## Personal data

Real documents (CV, job posts, recruiter emails) live in `my_docs/`, which git ignores. Never put them in `samples/` (committed, fictional only) and never commit them. The same goes for eval cases built from real sessions: they belong in `evals/private/` (git-ignored), never in `evals/answer_scoring/cases.yaml`, and the same applies to recordings in `evals/audio/`.

## HOW_IT_WORKS.md

The guide to the whole codebase for beginners and intermediates. Update it in the same change whenever the flow, a file's role, a command, the data model or the stack changes, and at the end of each phase (update "Where the project is").

## Commits

Commit with the identity set in this repo's local git config (`antoine-demyttenaere`); don't override it with another name or email. Don't add a `Co-Authored-By` line to commit messages.
