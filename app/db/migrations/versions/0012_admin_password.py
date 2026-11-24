"""Migration 0012: an optional password for admin accounts (Phase 4.8).

users.password_hash comes back, empty for everyone: only an admin can get one, with
`scripts/manage_users.py set-password EMAIL`, and only its Argon2 hash is stored
(app/auth/passwords.py). Everyone, admins included, can still log in with an emailed code.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(200)))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
