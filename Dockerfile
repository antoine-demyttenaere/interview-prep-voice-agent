# Dockerfile - recipe for the "api" image: a small Linux system with Python and the app's packages.
#
# Steps: start from the official slim Python 3.12 image, install requirements.txt, copy the code,
# and on start apply the database migrations, sync the question bank, then launch the web
# server (uvicorn).
# requirements.txt is copied and installed before the rest of the code so Docker can cache that
# slow step: changing your code does not reinstall the packages.
# Both Compose files replace the start command: docker-compose.yml adds --reload (local),
# docker-compose.prod.yml trusts Caddy's X-Forwarded-For (Phase 5, the AWS server).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Code is bind-mounted in dev (docker-compose.yml); copied for cloud images.
COPY . .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && (python scripts/seed_question_bank.py || echo 'Question bank not seeded') && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
