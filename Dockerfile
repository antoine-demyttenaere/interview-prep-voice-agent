# Image for the FastAPI app: installs Python deps, runs migrations, then starts uvicorn.
# Used by docker-compose.yml locally and by ECS in the cloud (Phase 5).

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
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
