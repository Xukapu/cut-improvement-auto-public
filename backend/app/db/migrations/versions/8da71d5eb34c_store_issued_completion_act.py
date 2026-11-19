"""Store the document data captured when a work order is issued."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8da71d5eb34c"
down_revision = "63dcab1e74f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("completion_act_data", postgresql.JSONB(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "completion_act_data")
