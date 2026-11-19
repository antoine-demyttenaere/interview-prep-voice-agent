"""The application package: everything the server runs lives under app/.

- main.py: the FastAPI entry point (start reading here).
- config.py: settings from .env.   llm.py: every call to Claude.
- api/: HTTP routes.   graph/: the interviewer (LangGraph).   db/: tables and migrations.

This file has no code: its presence makes app/ an importable Python package (`import app.main`).
"""
