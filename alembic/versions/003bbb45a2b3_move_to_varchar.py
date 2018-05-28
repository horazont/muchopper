"""move to varchar

Revision ID: 003bbb45a2b3
Revises: 66eba76d6fe2
Create Date: 2018-05-28 18:15:02.357599

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003bbb45a2b3'
down_revision = '66eba76d6fe2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.alter_column(
            "address",
            type_=sa.VARCHAR(3071),
            existing_type=sa.BLOB(3071),
        )

    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.alter_column(
            "address",
            type_=sa.VARCHAR(3071),
            existing_type=sa.BLOB(3071),
        )

    with op.batch_alter_table("domain") as batch_op:
        batch_op.alter_column(
            "domain",
            type_=sa.VARCHAR(1023),
            existing_type=sa.BLOB(1023),
        )


def downgrade():
    with op.batch_alter_table("muc") as batch_op:
        batch_op.alter_column(
            "address",
            type_=sa.BLOB(3071),
            existing_type=sa.VARCHAR(3071),
        )

    with op.batch_alter_table("public_muc") as batch_op:
        batch_op.alter_column(
            "address",
            type_=sa.BLOB(3071),
            existing_type=sa.VARCHAR(3071),
        )

    with op.batch_alter_table("domain") as batch_op:
        batch_op.alter_column(
            "domain",
            type_=sa.BLOB(1023),
            existing_type=sa.VARCHAR(1023),
        )
