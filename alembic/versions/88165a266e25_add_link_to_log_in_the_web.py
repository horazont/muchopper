"""add link to log in the web

Revision ID: 88165a266e25
Revises: db29c39a404f
Create Date: 2019-05-13 18:20:55.100022

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '88165a266e25'
down_revision = 'db29c39a404f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "http_logs_url",
                sa.Unicode(255),
                nullable=True,
            )
        )


def downgrade():
    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.drop_column("http_logs_url")
