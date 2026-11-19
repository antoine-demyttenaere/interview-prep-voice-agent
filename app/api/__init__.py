"""HTTP routes of the API, grouped into FastAPI "routers" that app/main.py mounts.

- interviews.py: create and read interviews, attach documents.
- sessions.py: start a practice session and answer each pause of the graph.
Every route checks its JSON body against a Pydantic model: a malformed body gets a 422 error
that explains what is wrong, before any of our code runs.
"""
