"""add software version info

Revision ID: 5ec473a3b9d1
Revises: 1eee6b9b5349
Create Date: 2018-10-15 17:40:48.280289

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5ec473a3b9d1'
down_revision = '1eee6b9b5349'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("domain") as batch_op:
        batch_op.add_column(
            sa.Column(
                "software_name",
                sa.Unicode(256),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "software_version",
                sa.Unicode(256),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "software_os",
                sa.Unicode(256),
                nullable=True,
            )
        )

    op.create_index(
        "domain_ix_software_name",
        "domain",
        ["software_name"],
    )


def downgrade():
    op.drop_index("domain_ix_software_name")

    with op.batch_alter_table("domain") as batch_op:
        batch_op.drop_column("software_name")
        batch_op.drop_column("software_version")
        batch_op.drop_column("software_os")

