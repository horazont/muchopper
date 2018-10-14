"""add indices to domain identity table

Revision ID: 1eee6b9b5349
Revises: b22b18f261e1
Create Date: 2018-10-14 21:51:12.001813

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1eee6b9b5349'
down_revision = 'b22b18f261e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "domain_identity_ix_domain_id",
        "domain_identity",
        ["domain_id"]
    )
    op.create_index(
        "domain_identity_ix_identity",
        "domain_identity",
        ["category", "type"]
    )


def downgrade():
    op.drop_index("domain_identity_ix_domain_id")
    op.drop_index("domain_identity_ix_identity")
