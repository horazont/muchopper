"""add avatar support

Revision ID: 05e34de3285d
Revises: 6b5f1d6591a2
Create Date: 2019-09-17 19:33:59.980689

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '05e34de3285d'
down_revision = '6b5f1d6591a2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "avatar",
        sa.Column(
            "address",
            sa.Unicode(3071),
            sa.ForeignKey("public_muc.address",
                          ondelete="CASCADE",
                          onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "mime_type",
            sa.Unicode(128),
            nullable=False,
        ),
        sa.Column(
            "hash",
            sa.Unicode(64),
            nullable=False,
        ),
        sa.Column(
            "data",
            sa.LargeBinary(),
            nullable=False,
        )
    )


def downgrade():
    op.drop_table(
        "avatar",
    )
