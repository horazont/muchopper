"""drop legacy columns

Revision ID: 66eba76d6fe2
Revises: ebbee0025c4d
Create Date: 2018-05-28 17:46:05.441669

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '66eba76d6fe2'
down_revision = 'ebbee0025c4d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.drop_column(
            "last_message_ts",
        )


def downgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_message_ts",
                sa.DateTime,
                nullable=True,
            )
        )
