"""Tests that each user sees only their own data (Phase 4.6 step 2).

Two accounts: A is the usual test user (tests/conftest.py), B a second account made here.
act_as() switches which one current_user returns, like logging in as someone else. They check that:
- every interview, document and session route answers 404 to B for A's ids (never 403, and
  nothing is changed), while A still has everything;
- B can't attach a document to A's interview, by text, file or link;
- B's CV list, B's brief and B's document search never contain A's CV, and A's search still
  finds it.

    docker compose exec api pytest -q tests/test_ownership.py
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_user
from app.db.models import User
from app.db.session import get_engine
from app.main import app
from app.rag.retrieve import search_documents

SECRET_A = "zanzibar quokka marmalade"  # words only A's CV contains
SECRET_B = "fjord pelican tangerine"  # words only B's CV contains


def act_as(user: User) -> None:
    app.dependency_overrides[current_user] = lambda: user


@pytest.fixture
def user_b() -> User:
    with DB(get_engine(), expire_on_commit=False) as db:
        user = User(email=f"b-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        db.commit()
        return user


def _interview_with_docs(client, cv_text: str) -> tuple[dict, dict]:
    iv = client.post("/interviews", json={"company": f"Co {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
    doc = client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post",
                                          "source": "post.txt", "text": "Python backend role"}).json()
    client.post("/documents", json={"doc_type": "cv", "source": "cv.txt", "text": f"My CV. {cv_text}."})
    return iv, doc


def test_other_users_ids_answer_404_and_change_nothing(fake, test_user, user_b):
    with TestClient(app) as client:
        iv, doc = _interview_with_docs(client, SECRET_A)
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()["session_id"]
        my_cv = {d["id"] for d in client.get("/documents/cv").json()}

        act_as(user_b)
        i, s = iv["id"], sid
        assert client.get(f"/interviews/{i}").status_code == 404
        assert client.patch(f"/interviews/{i}", json={"role": "Hacked"}).status_code == 404
        assert client.post(f"/interviews/{i}/sessions", json={}).status_code == 404
        assert client.get(f"/sessions/{s}").status_code == 404
        assert client.post(f"/sessions/{s}/resume", json={"value": {"action": "approve"}}).status_code == 404
        assert client.post(f"/sessions/{s}/retry").status_code == 404
        assert client.post(f"/sessions/{s}/end").status_code == 404
        assert client.post(f"/sessions/{s}/speak").status_code == 404
        assert client.post(f"/sessions/{s}/turn", files={"audio": ("a.webm", b"x", "audio/webm")}).status_code == 404
        assert client.delete(f"/documents/{doc['id']}").status_code == 404
        assert not my_cv & {d["id"] for d in client.get("/documents/cv").json()}

        # attaching a document to A's interview, three ways
        assert client.post("/documents", json={"interview_id": i, "doc_type": "notes", "text": "x"}).status_code == 404
        assert client.post("/documents/upload", data={"interview_id": i, "doc_type": "notes"},
                           files={"file": ("n.txt", b"some notes", "text/plain")}).status_code == 404
        assert client.post("/documents/from-url", json={"interview_id": i, "url": "https://example.com/job"}
                           ).status_code == 404

        act_as(test_user)  # A still has everything, unchanged
        mine = client.get(f"/interviews/{i}").json()
        assert mine["role"] == "Eng" and [d["id"] for d in mine["documents"]] == [doc["id"]]
        assert client.get(f"/sessions/{s}").json()["pending"]["type"] == "review_brief"


def test_brief_and_search_only_use_your_own_cv(fake, test_user, user_b):
    with TestClient(app) as client:
        iv_a, _ = _interview_with_docs(client, SECRET_A)

        act_as(user_b)
        iv_b, _ = _interview_with_docs(client, SECRET_B)
        sid = client.post(f"/interviews/{iv_b['id']}/sessions", json={"num_questions": 2}).json()["session_id"]
        state = app.state.graph.get_state({"configurable": {"thread_id": sid}}).values
        brief_texts = " ".join(d["text"] for d in state["documents"])
        assert SECRET_B in brief_texts and SECRET_A not in brief_texts

    found_b = " ".join(c["page_content"] for c in search_documents(iv_b["id"], SECRET_A, k=50))
    assert SECRET_A not in found_b
    found_a = " ".join(c["page_content"] for c in search_documents(iv_a["id"], SECRET_A, k=3))
    assert SECRET_A in found_a  # A's own search still finds A's CV
