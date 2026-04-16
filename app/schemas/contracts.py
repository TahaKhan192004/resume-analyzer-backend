from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, (set, tuple)):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [str(value)]


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


CSV_INPUT_COLUMNS = [
    "application_id",
    "received_at",
    "sender_name",
    "sender_email",
    "subject",
    "email_snippet",
    "gmail_message_id",
    "gmail_thread_id",
    "position_applied_from_email",
    "final_position_applied",
    "email_classification",
    "email_classification_confidence",
    "email_classification_reason",
    "has_attachment",
    "attachment_names",
    "selected_resume_file_name",
    "selected_resume_mime_type",
    "resume_storage_id",
    "resume_storage_link",
    "extraction_status",
    "extracted_resume_text_present",
    "candidate_full_name",
    "candidate_email_from_resume",
    "candidate_phone",
    "university",
    "degree",
    "major",
    "current_semester",
    "current_year_of_study",
    "expected_graduation_year",
    "currently_final_year",
    "education_status_label",
    "candidate_category",
    "skills_summary",
    "eligible_for_interview",
    "qualification_reason",
    "review_status",
    "candidate_stage",
    "invite_sent",
    "invite_sent_at",
    "processing_status",
    "processed_at",
    "notes",
]

CSV_OUTPUT_COLUMNS = [
    "resume_analysis_status",
    "final_candidate_score",
    "final_candidate_decision",
    "candidate_fit_summary",
    "top_strengths",
    "top_gaps",
    "best_project_relevance",
    "interview_recommendation",
    "interview_focus_areas",
    "ai_notes",
]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class RubricPayload(BaseModel):
    dimension: str
    weight: float = 1.0
    instructions: str = ""
    low_description: str = ""
    mid_description: str = ""
    high_description: str = ""
    red_flag_guidance: str = ""
    confidence_guidance: str = ""
    enabled: bool = True


class JobProfilePayload(BaseModel):
    title: str
    department: str | None = None
    employment_type: str | None = None
    role_level: str | None = None
    location: str | None = None
    status: str = "draft"
    description: str = ""
    summary: str = ""
    success_definition: str = ""
    responsibilities: str = ""
    practical_capabilities: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=lambda: {"shortlist": 75, "review": 55, "reject": 0})
    prompt_controls: dict[str, Any] = Field(default_factory=dict)
    rubrics: list[RubricPayload] = Field(default_factory=list)


class JobProfileRead(JobProfilePayload):
    id: UUID
    model_config = ConfigDict(from_attributes=True)


class JobProfileDraftRequest(BaseModel):
    description: str = Field(min_length=20)


class JobProfileDraftSchema(BaseModel):
    title: str = ""
    department: str = ""
    employment_type: str = ""
    role_level: str = ""
    location: str = ""
    summary: str = ""
    description: str = ""
    success_definition: str = ""
    responsibilities: str = ""
    practical_capabilities: str = ""
    essential_skills: list[str] = Field(default_factory=list)
    desirable_skills: list[str] = Field(default_factory=list)
    tools_platforms: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    preferred_projects: list[str] = Field(default_factory=list)
    preferred_ownership_level: str = ""
    expected_experience_depth: str = ""
    education_preferences: str = ""
    communication_expectations: str = ""


class PromptTemplatePayload(BaseModel):
    key: str
    name: str
    description: str = ""
    system_prompt: str
    task_prompt: str
    rubric_instructions: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)
    evaluation_hints: str = ""
    role_notes: str = ""
    model_name: str | None = None
    temperature: float = 0.1
    max_tokens: int = 2000


class ApplicantRead(BaseModel):
    id: UUID
    application_id: str | None
    candidate_name: str | None
    candidate_email: str | None
    applied_role: str | None
    processing_status: str
    review_status: str | None
    candidate_stage: str | None
    system_outputs: dict[str, Any]
    model_config = ConfigDict(from_attributes=True)


class ApplicantDetail(ApplicantRead):
    original_data: dict[str, Any]
    resume: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    dimension_results: list[dict[str, Any]] = Field(default_factory=list)
    final_evaluation: dict[str, Any] | None = None


class DimensionEvaluation(BaseModel):
    score: float = Field(ge=0, le=10)
    reasoning: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    red_flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    relevance_to_job: str = ""

    @field_validator("evidence", "red_flags", "missing_information", mode="before")
    @classmethod
    def coerce_text_lists(cls, value: Any) -> list[str]:
        return _coerce_text_list(value)


class ProjectAnalysis(DimensionEvaluation):
    projects: list[dict[str, Any]] = Field(default_factory=list)
    top_project_summary: str = ""

    @field_validator("projects", mode="before")
    @classmethod
    def coerce_project_list(cls, value: Any) -> list[dict[str, Any]]:
        return _coerce_dict_list(value)


class OwnershipAnalysis(DimensionEvaluation):
    ownership_category: str = ""


class ExperienceAnalysis(DimensionEvaluation):
    inferred_level: str = ""


class BatchDimensionResult(DimensionEvaluation):
    dimension: str
    projects: list[dict[str, Any]] = Field(default_factory=list)
    top_project_summary: str = ""
    ownership_category: str = ""
    inferred_level: str = ""

    @field_validator("projects", mode="before")
    @classmethod
    def coerce_project_list(cls, value: Any) -> list[dict[str, Any]]:
        return _coerce_dict_list(value)


class BatchDimensionEvaluations(BaseModel):
    results: list[BatchDimensionResult] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_common_batch_shapes(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"results": value}
        if not isinstance(value, dict):
            return value
        for key in ("results", "dimension_results", "evaluations", "dimensions"):
            if isinstance(value.get(key), list):
                return {"results": value[key]}
        converted = []
        known_dimensions = {
            "project_analysis",
            "project_complexity",
            "ownership",
            "skill_relevance",
            "experience_depth",
            "education_relevance",
            "communication_clarity",
            "growth_potential",
        }
        for key, item in value.items():
            if key in known_dimensions and isinstance(item, dict):
                converted.append({"dimension": key, **item})
        if converted:
            return {"results": converted}
        return value


class CandidateProfileSchema(BaseModel):
    candidate_name: str | None = None
    headline: str | None = None
    education_entries: list[dict[str, Any]] = Field(default_factory=list)
    experience_entries: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools_platforms: list[str] = Field(default_factory=list)
    inferred_domains: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    possible_seniority_indicators: list[str] = Field(default_factory=list)
    possible_ownership_indicators: list[str] = Field(default_factory=list)
    project_evidence_snippets: list[str] = Field(default_factory=list)
    ambiguity_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def accept_common_profile_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for wrapper in ("candidate_profile", "profile", "candidate"):
            nested = value.get(wrapper)
            if isinstance(nested, dict):
                value = nested
                break
        aliases = {
            "name": "candidate_name",
            "summary": "headline",
            "professional_summary": "headline",
            "education": "education_entries",
            "experience": "experience_entries",
            "work_experience": "experience_entries",
            "technical_skills": "skills",
            "tools": "tools_platforms",
            "technologies": "tools_platforms",
            "domains": "inferred_domains",
            "seniority_indicators": "possible_seniority_indicators",
            "ownership_indicators": "possible_ownership_indicators",
            "evidence_snippets": "project_evidence_snippets",
            "flags": "ambiguity_flags",
        }
        normalized = dict(value)
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        return normalized

    @field_validator("skills", "tools_platforms", "inferred_domains", "certifications", "achievements", "possible_seniority_indicators", "possible_ownership_indicators", "project_evidence_snippets", "ambiguity_flags", mode="before")
    @classmethod
    def coerce_text_lists(cls, value: Any) -> list[str]:
        return _coerce_text_list(value)

    @field_validator("education_entries", "experience_entries", "projects", mode="before")
    @classmethod
    def coerce_dict_lists(cls, value: Any) -> list[dict[str, Any]]:
        return _coerce_dict_list(value)


class FinalSynthesisSchema(BaseModel):
    final_candidate_score: float = Field(default=0, ge=0, le=100)
    final_candidate_confidence: float = Field(default=0.5, ge=0, le=1)
    final_candidate_decision: str = "review"
    candidate_fit_summary: str = ""
    top_strengths: list[str] = Field(default_factory=list)
    top_gaps: list[str] = Field(default_factory=list)
    best_project_relevance: str = ""
    interview_recommendation: str = "maybe"
    interview_focus_areas: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    ai_notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        aliases = {
            "final_score": "final_candidate_score",
            "score": "final_candidate_score",
            "confidence": "final_candidate_confidence",
            "final_confidence": "final_candidate_confidence",
            "decision": "final_candidate_decision",
            "summary": "candidate_fit_summary",
            "fit_summary": "candidate_fit_summary",
            "strengths": "top_strengths",
            "gaps": "top_gaps",
        }
        normalized = dict(value)
        for source, target in aliases.items():
            if target not in normalized and source in normalized:
                normalized[target] = normalized[source]
        return normalized

    @field_validator("top_strengths", "top_gaps", "interview_focus_areas", "red_flags", "missing_information", mode="before")
    @classmethod
    def coerce_text_lists(cls, value: Any) -> list[str]:
        return _coerce_text_list(value)
