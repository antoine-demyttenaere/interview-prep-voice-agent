# Interview Prep Voice Agent

A voice agent that runs realistic mock job interviews based on documents you give it about a company and a role, then gives you scored feedback.

You upload a job post, recruiter emails, call notes and your CV. Claude writes an interview brief, and you review it before the interview starts. An interviewer then asks you questions out loud and follows up when an answer is vague. At the end, it scores each answer against a rubric. You can practise in English, French or Spanish, by speaking or by typing.

## What it does

- **Four interview types:** behavioral / HR, technical backend, system design, and one tailored to a specific job post.
- **Grounded in your documents:** PDF, Word, `.eml` emails, text or a web page are split into chunks, embedded and searched for each question, so the questions reflect the real company and role.
- **You stay in control:** the interview pauses so you can read, edit or approve the brief before it starts, and you can end it early at any point.
- **Scored feedback:** each answer gets a 1-5 score per rubric criterion, and you get strengths, gaps and what to work on.
- **Open sign-up, with limits:** anyone can sign up with their email or their Google account. Each account gets a short practice (3 questions) and 2 full interviews, then can ask for more.
- **Cost-controlled:** every paid call is priced and recorded per user. Monthly and daily budgets stop new interviews (never a running one), each user has daily limits, and replaying a question is free.
- **Traceable and evaluated:** every turn is one LangSmith trace (speech-to-text, each graph step, the Claude calls, text-to-speech). A separate eval suite measures how well Claude scores answers on labelled cases.

## How it works

```
Browser (push-to-talk page)
   │ audio                                  ▲ audio + transcript
   ▼                                        │
Speech-to-text (Deepgram) ──► Interviewer graph (LangGraph + Claude) ──► Text-to-speech (ElevenLabs)
                                     │ state + retrieval
Uploads ──► Voyage embeddings ──► Postgres + pgvector              all traced in LangSmith
```

All interview types share one [LangGraph](https://langchain-ai.github.io/langgraph/) graph:

```
prepare_brief → review_brief (pause) → plan_questions → ask → answer (pause) → evaluate_answer ─┐
                                                         ▲                                      │
                                                         └──── next question or follow-up ◄─────┘
                                                                      … → final_feedback
```

- Each HTTP request runs the graph until its next pause and returns what it is waiting for. Your reply resumes it.
- The interview state is saved in Postgres after every step (the LangGraph checkpointer), so a session survives a restart or a failed call and can be retried.
- Interview types differ only in their files: a prompt in `prompts/modes/`, a scoring rubric in `rubrics/` and, for technical interviews, a question bank in `question_bank/`.
- Claude Haiku handles each spoken turn, for speed. Claude Sonnet writes the brief, the question plan and the final feedback.

For a beginner-friendly walkthrough of the whole codebase, read [HOW_IT_WORKS.md](HOW_IT_WORKS.md). The roadmap and design decisions are in [plan.md](plan.md).

## Stack

| Part | Choice |
| --- | --- |
| Reasoning | Claude (Haiku for turns, Sonnet for the brief and feedback) |
| Orchestration | LangGraph |
| API | FastAPI (Python) |
| Storage and vector search | PostgreSQL + pgvector |
| Embeddings | Voyage AI |
| Speech-to-text / text-to-speech | Deepgram / ElevenLabs, each behind an interface so a provider swap is a config change |
| Tracing and evals | LangSmith |
| Local run | Docker Compose |

## Run it locally

You need Docker Desktop and API keys for the services you want to use:

| Key | Needed for |
| --- | --- |
| `ANTHROPIC_API_KEY` | everything (required) |
| `VOYAGE_API_KEY` | document search and the question bank |
| `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY` | voice mode (typed mode works without them) |
| `LANGSMITH_API_KEY` | traces and evals |

```powershell
Copy-Item .env.example .env    # then fill in your keys in .env
docker compose up -d           # applies migrations, seeds the question bank, starts the server
docker compose exec api python scripts/create_user.py --email you@example.com --admin   # your admin account
```

Open http://localhost:8000 (Chrome, Edge or Firefox; allow the microphone) and enter your email. There are no passwords for users: the app emails you a 6-digit code (as admin, you can also set a password with `scripts/manage_users.py set-password`; "Continue" then asks for it). With `GOOGLE_CLIENT_ID` in `.env`, "Continue with Google" works too (see HOW_IT_WORKS.md, step 0b). Locally nothing is sent; the email is printed in `docker compose logs api`, where you read the code. Anyone can sign up the same way with their own email. The API docs are at http://localhost:8000/docs.

`scripts/manage_users.py` lists accounts, and can disable, enable or delete one.

To try it without your own documents, use the fictional CV and job post in [samples/](samples/). To practise in a terminal by typing (the first time, it asks for your email and the code it receives):

```powershell
python scripts/interview_cli.py new --company "Northwind Logistics" --role "Senior Backend Engineer" `
    --doc job_post:samples/job_post.txt --cv samples/cv.txt
```

## Tests and evals

```powershell
docker compose exec api pytest -q   # offline: fake Claude, Voyage and speech providers, real graph and Postgres
./scripts/eval.ps1                  # answer-scoring eval with real Claude (costs a little, ~2 min)
```

The tests check that the code works and make no API calls. The evals check how good Claude's decisions are: each labelled answer has an expected score range and an expected follow-up decision, and every run is recorded as a LangSmith experiment and compared with the previous run.

## Privacy

The app is meant to run on your own machine, and both ports are bound to `127.0.0.1`. Every interview route needs a login. There are no passwords for users: you log in, and sign up the first time, with a 6-digit code sent to your email (stored only as an HMAC) or with your Google account. The login cookie is HttpOnly and SameSite=Lax. Each user sees only their own interviews, documents and CV. Codes sent and wrong codes are rate-limited, requests sent from other websites are refused, and anyone can delete their account and all its data (confirmed by an emailed code). Your documents, answers and feedback are sent to Claude (Anthropic), Voyage AI (document search), Deepgram (speech to text) and ElevenLabs (the interviewer's voice) to run the interview.

- Your real documents go in `my_docs/`, eval cases built from your own answers go in `evals/private/`, and recordings go in `evals/audio/`. Git ignores all three.
- Everything in `samples/` and `evals/answer_scoring/cases.yaml` is fictional.
- Everything sent to Claude is also recorded in your LangSmith traces, which you can turn off with `LANGSMITH_TRACING=false`.

## Status

| Phase | Status |
| --- | --- |
| 0-3: skeleton, typed interview with brief review, voice, document uploads and search, technical interview types | built |
| 4: evals in LangSmith | in progress (answer-scoring eval done) |
| 4.6: accounts, each user sees only their own data | built |
| 4.7: sign up with an emailed code, 1 practice, 2 interviews, "Request more" | built (real email sending comes with Phase 5) |
| 4.8: admin login with a password, no per-user limits for admins | built |
| 4.9: one email field (admins get the password), Continue with Google, the Practiss logo, instant return to a brief | built |
| 4.5: cost control, per user | built |
| 5: deployment on AWS with Terraform | planned |
| 6: streaming, live turn detection, personas | planned |
