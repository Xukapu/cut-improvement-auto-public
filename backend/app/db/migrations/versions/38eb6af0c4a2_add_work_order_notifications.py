"""Align appointment cancellation constraints and link notifications to work orders."""

import sqlalchemy as sa
from alembic import op

revision: str = "38eb6af0c4a2"
down_revision: str | None = "246171d7b8c5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_appointments_status_valid"), "appointments", type_="check")
    op.create_check_constraint(
        op.f("ck_appointments_status_valid"),
        "appointments",
        "status IN ('scheduled', 'no_show', 'cancelled')",
    )

    op.drop_constraint(
        op.f("ck_customer_notifications_kind_valid"), "customer_notifications", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_customer_notifications_kind_valid"),
        "customer_notifications",
        "kind IN ('appointment_confirmation', 'appointment_reminder', "
        "'appointment_cancellation', 'service_reminder', 'work_order_ready')",
    )
    op.add_column("customer_notifications", sa.Column("work_order_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_customer_notifications_work_order_id_work_orders"),
        "customer_notifications",
        "work_orders",
        ["work_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_customer_notifications_work_order_id", "customer_notifications", ["work_order_id"]
    )


def downgrade() -> None:
    # Earlier versions cannot represent cancellations or ready notifications.
    # PostgreSQL refuses this downgrade if existing data would violate the old checks.
    op.drop_constraint(op.f("ck_appointments_status_valid"), "appointments", type_="check")
    op.create_check_constraint(
        op.f("ck_appointments_status_valid"), "appointments", "status IN ('scheduled', 'no_show')"
    )
    op.drop_constraint(
        op.f("ck_customer_notifications_kind_valid"), "customer_notifications", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_customer_notifications_kind_valid"),
        "customer_notifications",
        "kind IN ('appointment_confirmation', 'appointment_reminder', 'service_reminder')",
    )
    op.drop_index("ix_customer_notifications_work_order_id", table_name="customer_notifications")
    op.drop_constraint(
        op.f("fk_customer_notifications_work_order_id_work_orders"),
        "customer_notifications",
        type_="foreignkey",
    )
    op.drop_column("customer_notifications", "work_order_id")
