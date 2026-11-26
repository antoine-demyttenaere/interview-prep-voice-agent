"""Migration 0013: remember who signed in with Google.

users.google_sub holds Google's id for the person ("sub" in Google's ID token), set the first
time they use "Continue with Google" (POST /auth/google, app/auth/google.py). It is unique, so
one Google account opens one account here. Empty for everyone else.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(255)))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
