"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

import typing

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}


revision: typing.Final = ${repr(up_revision)}
down_revision: typing.Final = ${repr(down_revision)}
branch_labels: typing.Final = ${repr(branch_labels)}
depends_on: typing.Final = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
