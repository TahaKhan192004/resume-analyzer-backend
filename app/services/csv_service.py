from io import BytesIO, StringIO
import re
from typing import Any
from uuid import UUID

import pandas as pd
from sqlmodel import Session, select

from app.models.entities import Applicant, ApplicantImport, JobProfile, Resume
from app.schemas.contracts import CSV_INPUT_COLUMNS, CSV_OUTPUT_COLUMNS


CANONICAL_FIELD_ALIASES = {
    "application_id": (
        "application_id",
        "application id",
        "app id",
        "applicant id",
        "candidate id",
        "submission id",
        "response id",
        "id",
    ),
    "candidate_name": (
        "candidate_full_name",
        "candidate name",
        "full name",
        "name",
        "applicant name",
        "sender_name",
        "sender name",
    ),
    "candidate_email": (
        "candidate_email_from_resume",
        "candidate email",
        "email",
        "email address",
        "applicant email",
        "sender_email",
        "sender email",
    ),
    "candidate_phone": (
        "candidate_phone",
        "candidate phone",
        "phone",
        "phone number",
        "mobile",
        "mobile number",
        "contact number",
        "contact",
    ),
    "applied_role": (
        "final_position_applied",
        "position_applied_from_email",
        "applied role",
        "role applied",
        "position applied",
        "applied position",
        "job applied",
        "job title",
        "position",
        "role",
        "job",
        "opening",
        "vacancy",
    ),
    "received_at": (
        "received_at",
        "received at",
        "applied date",
        "application date",
        "submitted at",
        "submitted date",
        "created at",
        "date applied",
    ),
    "resume_storage_link": (
        "resume_storage_link",
        "resume link",
        "resume url",
        "cv link",
        "cv url",
        "attachment link",
        "file url",
        "document url",
        "resume",
        "cv",
    ),
    "linkedin_url": (
        "linkedin",
        "linked in",
        "linkedin url",
        "linkedin profile",
        "linkedin link",
        "profile url",
    ),
    "employment_status": (
        "employment_status",
        "employment status",
        "employment",
        "current employment",
        "current employment status",
    ),
    "selected_resume_file_name": (
        "selected_resume_file_name",
        "resume file name",
        "resume filename",
        "cv file name",
        "attachment name",
        "attachment_names",
        "file name",
    ),
    "selected_resume_mime_type": (
        "selected_resume_mime_type",
        "resume mime type",
        "cv mime type",
        "mime type",
        "content type",
    ),
    "extraction_status": (
        "extraction_status",
        "resume extraction status",
        "parser status",
    ),
    "review_status": (
        "review_status",
        "review status",
        "screening status",
        "status",
    ),
    "candidate_stage": (
        "candidate_stage",
        "candidate stage",
        "pipeline stage",
        "stage",
    ),
}


LEGACY_CANONICAL_KEYS = {
    "candidate_name": ("candidate_full_name", "sender_name"),
    "candidate_email": ("candidate_email_from_resume", "sender_email"),
    "applied_role": ("final_position_applied", "position_applied_from_email"),
    "candidate_phone": ("candidate_phone",),
    "received_at": ("received_at",),
}


def _clean(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _normalize_column(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _column_lookup(columns: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for column in columns:
        normalized = _normalize_column(column)
        if normalized and normalized not in lookup:
            lookup[normalized] = column
    return lookup


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _extract_field(original: dict[str, Any], lookup: dict[str, str], aliases: tuple[str, ...]) -> tuple[Any, str | None]:
    for alias in aliases:
        column = lookup.get(_normalize_column(alias))
        if not column:
            continue
        value = _clean(original.get(column))
        if _has_value(value):
            return value, column
    return None, None


def _canonicalize_row(
    original: dict[str, Any],
    lookup: dict[str, str],
    *,
    default_job_title: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    canonical: dict[str, Any] = {}
    mapping: dict[str, str] = {}
    for field, aliases in CANONICAL_FIELD_ALIASES.items():
        value, source_column = _extract_field(original, lookup, aliases)
        if _has_value(value):
            canonical[field] = value
        if source_column:
            mapping[field] = source_column

    if not canonical.get("applied_role") and default_job_title:
        canonical["applied_role"] = default_job_title
        mapping["applied_role"] = "selected_job_profile"

    normalized = dict(original)
    normalized["_canonical_import"] = canonical
    normalized["_column_mapping"] = mapping
    for field, value in canonical.items():
        normalized[field] = normalized.get(field) or value
    for field, keys in LEGACY_CANONICAL_KEYS.items():
        value = canonical.get(field)
        if not _has_value(value):
            continue
        for key in keys:
            normalized[key] = normalized.get(key) or value
    return normalized, mapping


def _find_existing_applicant(session: Session, *, application_id: str | None, candidate_email: str | None, applied_role: str | None) -> Applicant | None:
    if application_id:
        applicant = session.exec(select(Applicant).where(Applicant.application_id == application_id)).first()
        if applicant:
            return applicant
    if candidate_email and applied_role:
        return session.exec(
            select(Applicant).where(
                Applicant.candidate_email == candidate_email,
                Applicant.applied_role == applied_role,
            )
        ).first()
    return None


def _upsert_resume(session: Session, applicant: Applicant, original: dict[str, Any]) -> None:
    resume = session.exec(select(Resume).where(Resume.applicant_id == applicant.id)).first()
    if resume:
        resume.storage_link = resume.storage_link or original.get("resume_storage_link")
        resume.file_name = resume.file_name or original.get("selected_resume_file_name")
        resume.mime_type = resume.mime_type or original.get("selected_resume_mime_type")
        session.add(resume)
        return
    session.add(
        Resume(
            applicant_id=applicant.id,
            storage_link=original.get("resume_storage_link"),
            file_name=original.get("selected_resume_file_name"),
            mime_type=original.get("selected_resume_mime_type"),
            extraction_status=original.get("extraction_status") or "pending",
        )
    )


def import_applicant_csv(session: Session, *, data: bytes, file_name: str, job_id: UUID) -> tuple[ApplicantImport, list[UUID]]:
    df = pd.read_csv(BytesIO(data), dtype=str).where(pd.notnull, None)
    job = session.get(JobProfile, job_id)
    lookup = _column_lookup([str(column) for column in df.columns])
    import_record = ApplicantImport(job_id=job_id, file_name=file_name, row_count=len(df), status="imported")
    session.add(import_record)
    session.flush()
    applicant_ids: list[UUID] = []

    for _, row in df.iterrows():
        original_row = {column: _clean(row.get(column)) for column in df.columns}
        original, column_mapping = _canonicalize_row(original_row, lookup, default_job_title=job.title if job else None)
        application_id = original.get("application_id")
        candidate_name = original.get("candidate_name") or original.get("candidate_full_name") or original.get("sender_name")
        candidate_email = original.get("candidate_email") or original.get("candidate_email_from_resume") or original.get("sender_email")
        applied_role = original.get("applied_role") or original.get("final_position_applied") or original.get("position_applied_from_email")
        applicant = _find_existing_applicant(
            session,
            application_id=application_id,
            candidate_email=candidate_email,
            applied_role=applied_role,
        )
        if applicant:
            applicant.import_id = import_record.id
            applicant.original_data = {**(applicant.original_data or {}), **original}
            applicant.candidate_name = applicant.candidate_name or candidate_name
            applicant.candidate_email = applicant.candidate_email or candidate_email
            applicant.applied_role = applicant.applied_role or applied_role
            applicant.review_status = applicant.review_status or original.get("review_status")
            applicant.candidate_stage = applicant.candidate_stage or original.get("candidate_stage")
            applicant.processing_status = "queued"
            applicant.system_outputs = {
                **(applicant.system_outputs or {}),
                "resume_analysis_status": "queued",
                "csv_column_mapping": column_mapping,
            }
            session.add(applicant)
            session.flush()
            _upsert_resume(session, applicant, original)
        else:
            applicant = Applicant(
                import_id=import_record.id,
                job_id=job_id,
                application_id=application_id,
                candidate_name=candidate_name,
                candidate_email=candidate_email,
                applied_role=applied_role,
                original_data=original,
                processing_status="queued",
                system_outputs={"resume_analysis_status": "queued", "csv_column_mapping": column_mapping},
                review_status=original.get("review_status"),
                candidate_stage=original.get("candidate_stage"),
            )
            session.add(applicant)
            session.flush()
            _upsert_resume(session, applicant, original)
        applicant_ids.append(applicant.id)
    session.commit()
    session.refresh(import_record)
    return import_record, applicant_ids


def build_export_csv(session: Session, *, job_id: UUID, decision: str | None = None) -> str:
    query = select(Applicant).where(Applicant.job_id == job_id)
    applicants = session.exec(query).all()
    rows: list[dict[str, Any]] = []
    dynamic_input_columns = list(CSV_INPUT_COLUMNS)
    for applicant in applicants:
        outputs = applicant.system_outputs or {}
        if decision and outputs.get("final_candidate_decision") != decision:
            continue
        original_data = applicant.original_data or {}
        for column in original_data:
            if column.startswith("_") or column in dynamic_input_columns or column in CSV_OUTPUT_COLUMNS:
                continue
            dynamic_input_columns.append(column)
        row = {column: original_data.get(column) for column in dynamic_input_columns}
        for column in CSV_OUTPUT_COLUMNS:
            value = outputs.get(column)
            if isinstance(value, list):
                value = "; ".join(str(item) for item in value)
            row[column] = value
        rows.append(row)
    buffer = StringIO()
    pd.DataFrame(rows, columns=dynamic_input_columns + CSV_OUTPUT_COLUMNS).to_csv(buffer, index=False)
    return buffer.getvalue()
