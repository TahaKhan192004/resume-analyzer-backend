import re
from typing import Any

from app.models.entities import Applicant, JobProfile


ROLE_FIELDS = (
    "applied_role",
    "final_position_applied",
    "position_applied_from_email",
    "position",
    "job_title",
    "role",
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "the",
    "to",
    "role",
    "job",
    "position",
    "opening",
}


def normalize_role(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def role_tokens(value: Any) -> set[str]:
    return {token for token in normalize_role(value).split() if token and token not in STOP_WORDS}


def applicant_role_values(applicant: Applicant) -> list[str]:
    values = []
    if applicant.applied_role:
        values.append(applicant.applied_role)
    original = applicant.original_data or {}
    for field in ROLE_FIELDS:
        value = original.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    deduped = []
    seen = set()
    for value in values:
        normalized = normalize_role(value)
        if normalized and normalized not in seen:
            deduped.append(value)
            seen.add(normalized)
    return deduped


def applicant_matches_job(applicant: Applicant, job: JobProfile) -> tuple[bool, str]:
    if applicant.job_id == job.id:
        return True, ""

    job_tokens = role_tokens(job.title)
    if not job_tokens:
        return False, "The selected job has no comparable title."

    roles = applicant_role_values(applicant)
    if not roles:
        return False, "The applicant has no applied role stored."

    for role in roles:
        tokens = role_tokens(role)
        if not tokens:
            continue
        overlap = tokens & job_tokens
        smaller = min(len(tokens), len(job_tokens))
        if normalize_role(role) == normalize_role(job.title):
            return True, ""
        if smaller and len(overlap) / smaller >= 0.75:
            return True, ""

    return False, f"Applicant applied for {', '.join(roles)}, not {job.title}."
