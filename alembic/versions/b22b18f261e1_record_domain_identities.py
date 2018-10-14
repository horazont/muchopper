"""record domain identities

Revision ID: b22b18f261e1
Revises: ccb9a9f16150
Create Date: 2018-10-14 21:44:21.552940

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b22b18f261e1'
down_revision = 'ccb9a9f16150'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "domain_identity",
        sa.Column(
            "domain_id", sa.Integer,
            sa.ForeignKey("domain.id",
                          ondelete="CASCADE",
                          onupdate="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.String(64),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.String(64),
            primary_key=True,
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("domain_identity")
