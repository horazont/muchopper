"""create indexes

Revision ID: ebbee0025c4d
Revises: 7591d5f3c41c
Create Date: 2018-05-28 17:30:46.450826

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ebbee0025c4d'
down_revision = '7591d5f3c41c'
branch_labels = None
depends_on = None


def upgrade():
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
    op.drop_index("muc_ix_domain_id", "muc")
    op.drop_index("muc_ix_is_open", "muc")
    op.drop_index("muc_ix_nusers_moving_average", "muc")
