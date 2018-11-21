"""add delisting flag for domains

Revision ID: db29c39a404f
Revises: 905e0a73d396
Create Date: 2018-11-21 13:09:15.962015

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'db29c39a404f'
down_revision = '905e0a73d396'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("domain") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delisted",
                sa.Boolean(),
                nullable=True,
            )
        )

    domain = sa.sql.table(
        'domain',
        sa.sql.column('delisted')
    )

    op.execute(
        domain.update().values({'delisted': False})
    )

    with op.batch_alter_table("domain") as batch_op:
        batch_op.alter_column(
            "delisted",
            nullable=False,
            exsiting_type=sa.Boolean(),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("domain") as batch_op:
        batch_op.drop_column("delisted")
