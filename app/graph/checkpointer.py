"""Saves the graph's progress in Postgres, so a session can be stopped and resumed later.

A checkpointer stores a snapshot of the state after every node, under a thread_id (here: the
session id). Running the graph again with the same thread_id continues from the last snapshot,
even after a server restart. saver.setup() creates LangGraph's own tables (checkpoints, ...)
the first time and does nothing afterwards.

LangGraph's Postgres saver expects a psycopg connection pool with autocommit=True and rows
returned as dicts, which is what open_checkpointer() builds.
"""

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings


def open_checkpointer() -> tuple[ConnectionPool, PostgresSaver]:
    """Open a connection pool and a saver on it; creates the checkpoint tables if missing."""
    pool = ConnectionPool(
        conninfo=get_settings().psycopg_url,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()
    return pool, saver
