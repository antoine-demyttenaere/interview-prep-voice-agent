"""The interviewer: a LangGraph graph that runs one mock interview.

A graph is a set of steps (nodes) joined by arrows (edges). Each node is a Python function that
reads the shared state and returns the fields it changes. LangGraph runs the nodes in order,
saves the state after each one (the checkpointer) and can pause to wait for you (interrupt()).

- state.py: what the state holds.          - nodes.py: what each step does.
- build.py: how the steps are connected.   - schemas.py: shapes of Claude's structured answers.
- prompts.py: loads prompts/ and rubrics/.  - checkpointer.py: saves progress in Postgres.
"""
