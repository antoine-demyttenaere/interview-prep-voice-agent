"""Shared test helpers: a fake Claude and a fake embedder, so tests run free, fast and offline.

pytest loads conftest.py automatically.
- `fake`: any test that takes this argument gets a FakeClaude swapped into app/graph/nodes.py
  in place of the real structured() / text_reply().
- FakeEmbedder replaces Voyage in EVERY test (autouse), so no test ever calls the real API.
  Its vectors come from the words of the text (each word adds to one of the 1024 positions,
  chosen by a hash), so texts that share words are close: enough to test that search works.
- Tests use their own database, interview_test, created and migrated on the first run, so test
  interviews never mix with your real ones. DATABASE_URL is switched before the app reads it.
- Logged in by default: every test runs as the account tests@example.com, by replacing the
  current_user dependency (app/auth/deps.py) with that user (autouse fixture `logged_in`), so
  the interview tests don't have to log in. A test that takes the `real_auth` fixture turns this
  off and goes through the real cookie check (tests/test_auth.py).
- No leftover attempts: auth_attempts (the limits on codes sent and wrong codes) is emptied
  before each test, so attempts from one test or one earlier run never lock out the next.
- No real email: FakeMailer replaces app/mail in EVERY test (autouse fixture `mailbox`) and keeps
  each email in a list; mailbox.code_for(email) reads the latest 6-digit code sent there.
- Generous cost limits: the usage table is emptied before each test and the budgets and the
  "interviews per day" limit are set very high (fixture `generous_limits`), since the test
  account starts many interviews (it also has no practice and an endless interview quota). A
  test that checks a limit lowers it with monkeypatch (tests/test_costs.py), and the quota tests
  use fresh accounts (tests/test_quotas.py).
"""

import hashlib
import math
import os
import re

# Must run before anything calls app.config.get_settings(), i.e. before the app imports below.
_REAL_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5433/interview")
TEST_DB = "interview_test"
os.environ["DATABASE_URL"] = _REAL_URL.rsplit("/", 1)[0] + "/" + TEST_DB

import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.deps import current_user  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import AuthAttempt, Usage, User  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.graph import nodes  # noqa: E402
from app.main import app  # noqa: E402
from app.rag import embeddings  # noqa: E402
from app.graph.schemas import (  # noqa: E402
    AnswerEvaluation,
    CriterionScore,
    FinalFeedback,
    InterviewBrief,
    PlannedQuestion,
    QuestionPlan,
)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Create the interview_test database if needed and bring it to the latest migration."""
    admin_url = _REAL_URL.replace("postgresql+psycopg://", "postgresql://").rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    command.upgrade(Config("alembic.ini"), "head")
    yield


TEST_EMAIL = "tests@example.com"


@pytest.fixture(scope="session")
def test_user(test_database) -> User:
    """The account the tests run as (created once, reused by every run). It has no practice and an
    endless interview quota, so every session is a plain interview, as the older tests expect;
    the quota tests use fresh accounts (tests/test_quotas.py)."""
    with DB(get_engine(), expire_on_commit=False) as db:
        user = db.scalar(select(User).where(User.email == TEST_EMAIL))
        if not user:
            user = User(email=TEST_EMAIL)
            db.add(user)
        user.practice_quota, user.interview_quota = 0, 1_000_000
        db.commit()
        return user


class FakeMailer:
    """Stands in for app/mail: keeps every email instead of sending it."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, text):
        self.sent.append({"to": to, "subject": subject, "text": text})

    def code_for(self, email: str) -> str:
        """The latest 6-digit code emailed to this address."""
        mails = [m for m in self.sent if m["to"] == email.strip().lower()]
        assert mails, f"no email was sent to {email}"
        return re.search(r"\b(\d{6})\b", mails[-1]["text"]).group(1)


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    from app.api import usage as usage_api
    from app.auth import routes as auth_routes

    box = FakeMailer()
    monkeypatch.setattr(auth_routes, "get_mailer", lambda: box)
    monkeypatch.setattr(usage_api, "get_mailer", lambda: box)
    return box


@pytest.fixture(autouse=True)
def no_auth_attempts(test_database):
    with DB(get_engine()) as db:
        db.execute(delete(AuthAttempt))
        db.commit()


@pytest.fixture(autouse=True)
def generous_limits(test_database, monkeypatch):
    with DB(get_engine()) as db:
        db.execute(delete(Usage))
        db.commit()
    s = get_settings()
    monkeypatch.setattr(s, "interviews_per_day", 100_000)
    for name in ("monthly", "daily", "claude", "elevenlabs", "deepgram", "voyage", "practice_monthly", "practice_daily"):
        monkeypatch.setattr(s, f"budget_{name}_usd", 1_000_000.0)


@pytest.fixture(autouse=True)
def logged_in(test_user):
    """Every request is made as test_user, without a cookie (see the module docstring)."""
    app.dependency_overrides[current_user] = lambda: test_user
    yield
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture
def real_auth(logged_in):
    """Turns `logged_in` off: requests must carry a real login cookie."""
    app.dependency_overrides.pop(current_user, None)

BRIEF = InterviewBrief(
    company_summary="Fake Co makes fakes.",
    role_requirements=["Python"],
    interview_format="One hour",
    interviewers=[],
    cv_gaps=["none"],
    focus_areas=["ownership"],
    probable_questions=["Tell me about a failure"],
)


class FakeClaude:
    """Stands in for app.llm.structured / text_reply. Follows up when an answer says 'vague'."""

    def __init__(self):
        self.calls: list[str] = []
        self.systems: list[str] = []  # system prompts sent, to check the language
        self.asks: list[str] = []  # ask prompts sent, to check the retrieved context
        self.models: dict[str, str] = {}  # schema name -> model asked for (e.g. Haiku for a practice's feedback)

    def structured(self, *, schema, user, system, model=None, **_):
        self.calls.append(schema.__name__)
        self.models[schema.__name__] = model
        self.systems.append(system)
        if schema is InterviewBrief:
            return BRIEF
        if schema is QuestionPlan:
            return QuestionPlan(
                questions=[
                    PlannedQuestion(text=f"Q{i}", topic="t", rationale="r", based_on="brief") for i in range(1, 4)
                ]
            )
        if schema is AnswerEvaluation:
            last_answer = user.rsplit("Candidate:", 1)[-1].split("</exchange>", 1)[0]
            vague = "vague" in last_answer
            return AnswerEvaluation(
                scores=[CriterionScore(criterion="structure", score=2 if vague else 4, note="n")],
                overall=2 if vague else 4,
                summary="s",
                follow_up=vague,
                follow_up_focus="give details" if vague else "",
            )
        if schema is FinalFeedback:
            return FinalFeedback(
                overall_score=3, summary="ok", rubric_scores=[], per_question=[], strengths=["a"], fixes=["b"]
            )
        raise AssertionError(schema)

    def text_reply(self, *, user, **_):
        self.calls.append("ask")
        self.asks.append(user)
        return "follow-up?" if "needs a follow-up" in user else "next question?"


@pytest.fixture
def fake(monkeypatch):
    f = FakeClaude()
    monkeypatch.setattr(nodes, "structured", f.structured)
    monkeypatch.setattr(nodes, "text_reply", f.text_reply)
    return f


class FakeEmbedder:
    """Bag-of-words vectors: deterministic, offline, and similar texts get similar vectors."""

    def embed(self, texts, input_type):
        vectors = []
        for text in texts:
            v = [0.0] * embeddings.DIMENSIONS
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                if len(word) >= 2:
                    v[int(hashlib.md5(word.encode()).hexdigest(), 16) % embeddings.DIMENSIONS] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    f = FakeEmbedder()
    monkeypatch.setattr(embeddings, "get_embedder", lambda: f)
    return f
