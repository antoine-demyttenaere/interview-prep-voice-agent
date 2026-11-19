"""Tests of the voice routes (/turn and /speak) with fake speech providers and a fake Claude.

FakeSTT and FakeTTS replace Deepgram and ElevenLabs (monkeypatch swaps get_stt / get_tts in
app/api/sessions.py), so these run free and offline. They check that a recording goes through
speech-to-text, the graph and text-to-speech, that the language is passed to both providers,
and that failures (silence, a provider error) leave the session waiting on the same question.

    docker compose exec api pytest -q tests/test_voice_turn.py
"""

import base64
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import sessions
from app.voice import SpeechError
from app.voice.stt import Transcript
from app.voice.tts import Speech


class FakeSTT:
    def __init__(self):
        self.heard = "good answer"  # what the next recording "says"
        self.fail = False
        self.languages: list[str] = []

    def transcribe(self, audio, mime_type, language):
        self.languages.append(language)
        if self.fail:
            raise SpeechError("Deepgram error 401: bad key")
        return Transcript(text=self.heard, confidence=0.9)


class FakeTTS:
    def __init__(self):
        self.said: list[tuple[str, str]] = []

    def synthesize(self, text, language):
        self.said.append((text, language))
        return Speech(audio=b"MP3" + text.encode(), mime_type="audio/mpeg")


@pytest.fixture
def speech(monkeypatch):
    stt, tts = FakeSTT(), FakeTTS()
    monkeypatch.setattr(sessions, "get_stt", lambda: stt)
    monkeypatch.setattr(sessions, "get_tts", lambda: tts)
    return stt, tts


def _session_waiting_for_answer(client, language="en"):
    iv = client.post("/interviews", json={
        "company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng", "language": language,
    }).json()
    client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
    view = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
    view = client.post(f"/sessions/{view['session_id']}/resume", json={"value": {"action": "approve"}}).json()
    assert view["pending"]["type"] == "answer"
    return view["session_id"]


def _record(client, sid):
    return client.post(f"/sessions/{sid}/turn", files={"audio": ("answer.webm", b"fake-audio", "audio/webm")})


def test_voice_turn_round_trip(fake, speech):
    stt, tts = speech
    from app.main import app

    with TestClient(app) as client:
        sid = _session_waiting_for_answer(client, language="fr")

        spoken = client.post(f"/sessions/{sid}/speak")
        assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/mpeg"

        reply = _record(client, sid)
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body["you_said"] == "good answer"
        assert body["pending"]["q_index"] == 1  # moved on to question 2
        assert base64.b64decode(body["audio"]["base64"]).startswith(b"MP3")
        assert set(body["timings_ms"]) == {"speech_to_text", "interviewer", "text_to_speech", "total"}
        assert body["transcript"][-1]["role"] == "interviewer"
        assert stt.languages == ["fr"] and all(lang == "fr" for _, lang in tts.said)

        body = _record(client, sid).json()  # last answer -> final feedback, nothing to say
        assert body["status"] == "done" and body["audio"] is None


def test_silence_and_provider_errors_keep_the_question(fake, speech):
    stt, _ = speech
    from app.main import app

    with TestClient(app) as client:
        sid = _session_waiting_for_answer(client)

        stt.heard = ""
        assert _record(client, sid).status_code == 422

        stt.heard, stt.fail = "good answer", True
        assert _record(client, sid).status_code == 502

        view = client.get(f"/sessions/{sid}").json()
        assert view["status"] == "waiting" and view["pending"]["q_index"] == 0

        stt.fail = False
        assert _record(client, sid).json()["pending"]["q_index"] == 1


def test_turn_refused_when_not_waiting_for_an_answer(fake, speech):
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": "Fake", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={}).json()["session_id"]  # at brief review
        assert _record(client, sid).status_code == 409
        assert client.post(f"/sessions/{sid}/speak").status_code == 409
