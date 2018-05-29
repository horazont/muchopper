"""initialize database

Revision ID: 7591d5f3c41c
Revises:
Create Date: 2018-05-28 17:14:50.907775

"""
from alembic import op
import sqlalchemy as sa

from muchopper.common.model import JID


# revision identifiers, used by Alembic.
revision = '7591d5f3c41c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "domain",
        sa.Column("id", sa.Integer,
                  primary_key=True,
                  nullable=False,
                  autoincrement=True),
        sa.Column("domain",
                  sa.LargeBinary(1023),
                  unique=True,
                  nullable=False),
    )

    op.create_table(
        "muc",
        sa.Column("address", sa.LargeBinary(3071),
                  primary_key=True,
                  nullable=False),
        sa.Column("domain_id", sa.Integer,
                  sa.ForeignKey("domain.id",
                                ondelete="CASCADE",
                                onupdate="CASCADE"),
                  nullable=False),
        sa.Column("nusers", sa.Integer,
                  nullable=True),
        sa.Column("nusers_moving_average", sa.Float,
                  nullable=True),
        sa.Column("moving_average_last_update", sa.DateTime,
                  nullable=True),
        sa.Column("last_message_ts", sa.DateTime,
                  nullable=True),
        sa.Column("is_open", sa.Boolean,
                  nullable=False),
        sa.Column("was_kicked", sa.Boolean,
                  nullable=False),
    )

    op.create_table(
        "public_muc",
        sa.Column("address", sa.LargeBinary(3071),
                  sa.ForeignKey("muc.address",
                                ondelete="CASCADE",
                                onupdate="CASCADE"),
                  primary_key=True,
                  nullable=False),
        sa.Column("subject", sa.Unicode,
                  nullable=True),
        sa.Column("name", sa.Unicode,
                  nullable=True),
        sa.Column("description", sa.Unicode,
                  nullable=True),
    )


def downgrade():
    pass
