from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class EvaluationStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    parsing = "parsing"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    missing_resume = "missing_resume"


class Decision(str, Enum):
    shortlist = "shortlist"
    review = "review"
    reject = "reject"


class CandidateEmailStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    failed = "failed"


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, unique=True)
    full_name: str
    password_hash: str
    role: str = "admin"
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class JobProfile(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(index=True)
    department: str | None = None
    employment_type: str | None = None
    role_level: str | None = None
    location: str | None = None
    status: JobStatus = JobStatus.draft
    description: str = ""
    summary: str = ""
    success_definition: str = ""
    responsibilities: str = ""
    practical_capabilities: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    thresholds: dict[str, Any] = Field(default_factory=lambda: {"shortlist": 75, "review": 55, "reject": 0}, sa_column=Column(JSONB))
    prompt_controls: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    rubrics: list["JobRubric"] = Relationship(back_populates="job")


class JobRubric(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="jobprofile.id", index=True)
    dimension: str = Field(index=True)
    weight: float = 1.0
    instructions: str = ""
    low_description: str = ""
    mid_description: str = ""
    high_description: str = ""
    red_flag_guidance: str = ""
    confidence_guidance: str = ""
    enabled: bool = True
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    job: JobProfile | None = Relationship(back_populates="rubrics")


class PromptTemplate(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    active_version_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PromptTemplateVersion(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    template_id: UUID = Field(foreign_key="prompttemplate.id", index=True)
    version: int
    system_prompt: str
    task_prompt: str
    rubric_instructions: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    evaluation_hints: str = ""
    role_notes: str = ""
    model_name: str | None = None
    temperature: float = 0.1
    max_tokens: int = 2000
    created_by: UUID | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utc_now)


class ApplicantImport(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="jobprofile.id", index=True)
    file_name: str
    row_count: int = 0
    status: str = "created"
    created_by: UUID | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utc_now)


class Applicant(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    import_id: UUID | None = Field(default=None, foreign_key="applicantimport.id", index=True)
    job_id: UUID = Field(foreign_key="jobprofile.id", index=True)
    application_id: str | None = Field(default=None, index=True)
    candidate_name: str | None = Field(default=None, index=True)
    candidate_email: str | None = Field(default=None, index=True)
    applied_role: str | None = None
    original_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    system_outputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    processing_status: EvaluationStatus = EvaluationStatus.pending
    review_status: str | None = None
    candidate_stage: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Resume(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    storage_link: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    extraction_status: str = "pending"
    extracted_text: str | None = None
    parsed_sections: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    parser_diagnostics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CandidateProfile(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    resume_id: UUID | None = Field(default=None, foreign_key="resume.id")
    profile_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    prompt_version_id: UUID | None = Field(default=None, foreign_key="prompttemplateversion.id")
    model_name: str | None = None
    confidence: float | None = None
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationRun(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    job_id: UUID = Field(foreign_key="jobprofile.id", index=True)
    status: EvaluationStatus = EvaluationStatus.pending
    reason: str | None = None
    rubric_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    prompt_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationDimensionResult(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    run_id: UUID = Field(foreign_key="evaluationrun.id", index=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    dimension: str = Field(index=True)
    score: float | None = None
    confidence: float | None = None
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    prompt_version_id: UUID | None = Field(default=None, foreign_key="prompttemplateversion.id")
    model_name: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    raw_response: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utc_now)


class FinalEvaluation(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    run_id: UUID = Field(foreign_key="evaluationrun.id", index=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    final_score: float
    final_confidence: float | None = None
    decision: Decision
    interview_recommendation: str = "maybe"
    summary: str = ""
    strengths: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    gaps: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    best_project_relevance: str = ""
    interview_focus_areas: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    red_flags: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    missing_information: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    synthesis_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utc_now)


class CandidateEmail(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    applicant_id: UUID = Field(foreign_key="applicant.id", index=True)
    job_id: UUID = Field(foreign_key="jobprofile.id", index=True)
    run_id: UUID = Field(foreign_key="evaluationrun.id", index=True)
    final_evaluation_id: UUID = Field(foreign_key="finalevaluation.id", index=True)
    to_email: str = Field(index=True)
    from_email: str
    subject: str
    body: str
    status: CandidateEmailStatus = Field(default=CandidateEmailStatus.draft, index=True)
    failure_reason: str | None = None
    sent_at: datetime | None = None
    created_by: UUID | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuditLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    actor_id: UUID | None = Field(default=None, foreign_key="user.id")
    entity_type: str
    entity_id: UUID | None = None
    action: str
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utc_now)
