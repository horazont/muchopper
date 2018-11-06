"""add anonymity flag to mucs

Revision ID: 905e0a73d396
Revises: 5ec473a3b9d1
Create Date: 2018-11-06 21:31:06.954092

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '905e0a73d396'
down_revision = '5ec473a3b9d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "anonymity_mode",
                sa.Unicode(32),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.drop_column("anonymity_mode")
