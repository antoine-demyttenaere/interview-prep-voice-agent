"""Loads question_bank/*.yaml into the question_bank table and embeds the questions with Voyage.

It keeps the table in sync with the files: new questions are added, questions removed from a
file are deleted, and only questions without an embedding are sent to Voyage, so running it
again costs nothing. The api container runs it on every start (docker-compose.yml); if Voyage
is unreachable it prints why and the server starts anyway (the bank is then simply empty).

Run by hand after editing a YAML file:
    docker compose exec api python scripts/seed_question_bank.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.db.models import QuestionBankItem  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.rag import embeddings  # noqa: E402

BANK_DIR = Path(__file__).resolve().parents[1] / "question_bank"


def load_files() -> dict[tuple[str, str], dict]:
    wanted = {}
    for path in sorted(BANK_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for q in data["questions"]:
            wanted[(data["mode"], q["text"].strip())] = q
    return wanted


def main() -> int:
    wanted = load_files()
    with DB(get_engine()) as db:
        existing = {(r.mode, r.text): r for r in db.scalars(select(QuestionBankItem))}
        removed = [r for key, r in existing.items() if key not in wanted]
        for row in removed:
            db.delete(row)
        added = 0
        for (mode, text), q in wanted.items():
            row = existing.get((mode, text))
            if row is None:
                db.add(QuestionBankItem(mode=mode, text=text, topic=q["topic"], difficulty=q["difficulty"]))
                added += 1
            else:
                row.topic, row.difficulty = q["topic"], q["difficulty"]
        db.commit()

        missing = list(db.scalars(select(QuestionBankItem).where(QuestionBankItem.embedding.is_(None))))
        if missing:
            try:
                vectors = embeddings.get_embedder().embed([r.text for r in missing], "document")
            except embeddings.EmbeddingError as exc:
                print(f"Question bank: {added} added, {len(removed)} removed, NOT embedded: {exc}")
                return 1
            for row, vector in zip(missing, vectors):
                row.embedding = vector
            db.commit()
    print(f"Question bank: {len(wanted)} questions ({added} added, {len(removed)} removed, {len(missing)} embedded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
