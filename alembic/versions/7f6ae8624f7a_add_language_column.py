"""add language column

Revision ID: 7f6ae8624f7a
Revises: b33ed96e988d
Create Date: 2018-05-29 17:21:01.619509

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f6ae8624f7a'
down_revision = 'b33ed96e988d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "language",
                sa.Unicode(32),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.drop_column("language")
