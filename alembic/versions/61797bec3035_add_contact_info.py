"""add contact info

Revision ID: 61797bec3035
Revises: 34573711035b
Create Date: 2024-02-29 17:47:23.279669

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '61797bec3035'
down_revision = '34573711035b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "domain_contact",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "domain_id",
            sa.Integer(),
            sa.ForeignKey("domain.id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Unicode(32),
            nullable=False,
        ),
        sa.Column(
            "address",
            sa.Unicode(1023),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("domain_contact")
