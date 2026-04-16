"""candidate rejection emails

Revision ID: 0002_candidate_rejection_emails
Revises: 0001_initial
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0002_candidate_rejection_emails"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "candidateemail" in inspector.get_table_names():
        return

    status_enum = postgresql.ENUM("draft", "sent", "failed", name="candidateemailstatus", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "candidateemail",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("final_evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("to_email", sa.String(), nullable=False),
        sa.Column("from_email", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["applicant_id"], ["applicant.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["final_evaluation_id"], ["finalevaluation.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobprofile.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["evaluationrun.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_candidateemail_applicant_id"), "candidateemail", ["applicant_id"], unique=False)
    op.create_index(op.f("ix_candidateemail_final_evaluation_id"), "candidateemail", ["final_evaluation_id"], unique=False)
    op.create_index(op.f("ix_candidateemail_job_id"), "candidateemail", ["job_id"], unique=False)
    op.create_index(op.f("ix_candidateemail_run_id"), "candidateemail", ["run_id"], unique=False)
    op.create_index(op.f("ix_candidateemail_status"), "candidateemail", ["status"], unique=False)
    op.create_index(op.f("ix_candidateemail_to_email"), "candidateemail", ["to_email"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_candidateemail_to_email"), table_name="candidateemail")
    op.drop_index(op.f("ix_candidateemail_status"), table_name="candidateemail")
    op.drop_index(op.f("ix_candidateemail_run_id"), table_name="candidateemail")
    op.drop_index(op.f("ix_candidateemail_job_id"), table_name="candidateemail")
    op.drop_index(op.f("ix_candidateemail_final_evaluation_id"), table_name="candidateemail")
    op.drop_index(op.f("ix_candidateemail_applicant_id"), table_name="candidateemail")
    op.drop_table("candidateemail")
    postgresql.ENUM(name="candidateemailstatus").drop(op.get_bind(), checkfirst=True)
