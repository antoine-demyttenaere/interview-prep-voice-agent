"""Cost control (Phase 4.5): what each paid call costs, who it was for, and the budgets.

- pricing.py  reads pricing.yaml and turns what a call used into dollars
- usage.py    records one row per paid call in the usage table, for the right user and session
- budget.py   adds the rows up: refuses new sessions over budget, enforces the per-user limits

Every paid call already goes through one place per provider: app/llm.py (Claude),
app/voice/stt.py (Deepgram), app/voice/tts.py (ElevenLabs), app/rag/embeddings.py (Voyage).
Each of them calls usage.record_*() after a successful response. The API routes say whose
call it is with `with usage.attribute(user_id, session_id):`.
"""
