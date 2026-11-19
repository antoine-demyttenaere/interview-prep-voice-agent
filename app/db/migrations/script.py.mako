## script.py.mako - the template Alembic fills in to create each new migration file
## (`alembic revision -m "..."`). Alembic replaces the ${...} parts: message, revision ids, date.
## The new file has an upgrade() (apply the change) and a downgrade() (undo it) for you to write.
## Lines starting with ## are Mako comments and are not copied into the output.
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
