"""add last_seen fields

Revision ID: ccb9a9f16150
Revises: 7f6ae8624f7a
Create Date: 2018-07-16 08:50:55.600474

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ccb9a9f16150'
down_revision = '7f6ae8624f7a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("domain") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_seen",
                sa.DateTime(),
                nullable=True,
            )
        )

    with op.batch_alter_table("muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_seen",
                sa.DateTime(),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("domain") as batch_op:
        batch_op.drop_column("last_seen")

    with op.batch_alter_table("muc") as batch_op:
        batch_op.drop_column("last_seen")
