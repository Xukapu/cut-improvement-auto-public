"""Track completion of individual work items.

Revision ID: 63dcab1e74f2
Revises: 38eb6af0c4a2
"""

import sqlalchemy as sa
from alembic import op

revision = "63dcab1e74f2"
down_revision = "38eb6af0c4a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Issuance is the available historical evidence of completion. Individual
    # work completion dates from before this migration were never recorded.
    op.execute(
        """
        UPDATE work_items AS work
        SET completed_at = orders.issued_at
        FROM work_orders AS orders
        WHERE work.work_order_id = orders.id
          AND work.deleted_at IS NULL
          AND orders.deleted_at IS NULL
          AND orders.status = 'issued'
          AND orders.issued_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("work_items", "completed_at")
