"""initialize database

Revision ID: 0c17fe0f84bd
Revises:
Create Date: 2018-05-29 07:32:05.104046

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0c17fe0f84bd'
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
                  sa.Unicode(1023),
                  unique=True,
                  nullable=False),
    )

    op.create_table(
        "muc",
        sa.Column("address", sa.Unicode(3071),
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
        sa.Column("is_open", sa.Boolean,
                  nullable=False),
        sa.Column("was_kicked", sa.Boolean,
                  nullable=False),
    )

    op.create_table(
        "public_muc",
        sa.Column("address", sa.Unicode(3071),
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

    op.create_index(
        "muc_ix_domain_id",
        "muc",
        [
            "domain_id",
        ]
    )

    op.create_index(
        "muc_ix_is_open",
        "muc",
        [
            "is_open",
        ]
    )

    op.create_index(
        "muc_ix_nusers_moving_average",
        "muc",
        [
            "nusers_moving_average",
        ],
    )


def downgrade():
    pass
