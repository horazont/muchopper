"""add is_hidden column

Revision ID: b33ed96e988d
Revises: 0c17fe0f84bd
Create Date: 2018-05-29 13:31:18.588144

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b33ed96e988d'
down_revision = '0c17fe0f84bd'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_hidden",
                sa.Boolean(),
                nullable=False,
                default=False,
                server_default="FALSE",
            )
        )


def downgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.drop_column("is_hidden")
