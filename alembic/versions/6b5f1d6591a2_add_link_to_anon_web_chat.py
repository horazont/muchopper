"""add link to anon web chat

Revision ID: 6b5f1d6591a2
Revises: 88165a266e25
Create Date: 2019-09-17 16:39:14.448568

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6b5f1d6591a2'
down_revision = '88165a266e25'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "web_chat_url",
                sa.Unicode(2047),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.drop_column("web_chat_url")
