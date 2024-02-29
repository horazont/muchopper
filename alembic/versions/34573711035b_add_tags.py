"""add tags

Revision ID: 34573711035b
Revises: 05e34de3285d
Create Date: 2024-02-21 17:44:35.186903

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '34573711035b'
down_revision = '05e34de3285d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tag",
        sa.Column(
            "key", sa.Unicode(128),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_table(
        "public_muc_tags",
        sa.Column(
            "tag", sa.Unicode(128),
            sa.ForeignKey("tag.key",
                          ondelete="CASCADE",
                          onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "public_muc", sa.Unicode(3071),
            sa.ForeignKey("public_muc.address",
                          ondelete="CASCADE",
                          onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "public_muc_tags_ix_public_muc",
        "public_muc_tags",
        ["public_muc"]
    )
    op.create_index(
        "public_muc_tags_ix_tag",
        "public_muc_tags",
        ["tag"]
    )


def downgrade():
    op.drop_index("public_muc_tags_ix_public_muc")
    op.drop_index("public_muc_tags_ix_tag")
    op.drop_table("public_muc_tags")
    op.drop_table("tag")
