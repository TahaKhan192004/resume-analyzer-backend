import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.entities import (
    Applicant,
    CandidateProfile,
    Decision,
    EvaluationDimensionResult,
    EvaluationRun,
    EvaluationStatus,
    FinalEvaluation,
    JobProfile,
    JobRubric,
    PromptTemplate,
    PromptTemplateVersion,
    Resume,
)
from app.schemas.contracts import (
    BatchDimensionEvaluations,
    CandidateProfileSchema,
    DimensionEvaluation,
    ExperienceAnalysis,
    FinalSynthesisSchema,
    OwnershipAnalysis,
    ProjectAnalysis,
)
from app.services.llm_client import DeepSeekClient, LLMError
from app.services.resume_parser import ResumeParsingError, extract_text_from_bytes, fetch_resume_bytes

DIMENSION_SCHEMAS: dict[str, type[DimensionEvaluation]] = {
    "project_analysis": ProjectAnalysis,
    "project_complexity": DimensionEvaluation,
    "ownership": OwnershipAnalysis,
    "skill_relevance": DimensionEvaluation,
    "experience_depth": ExperienceAnalysis,
    "education_relevance": DimensionEvaluation,
    "communication_clarity": DimensionEvaluation,
    "growth_potential": DimensionEvaluation,
}

DEFAULT_ENABLED_DIMENSIONS = [
    "project_analysis",
    "project_complexity",
    "ownership",
    "skill_relevance",
    "experience_depth",
    "education_relevance",
    "communication_clarity",
    "growth_potential",
]

BATCH_DIMENSION_SYSTEM_PROMPT = (
    "You are a rigorous senior recruiter and technical evaluator. Return JSON only. "
    "Evaluate evidence quality, job relevance, project maturity, candidate ownership, practical skill depth, and uncertainty. "
    "Do not invent facts. Use the candidate profile as the evidence source. If evidence is vague or missing, lower confidence."
)

BATCH_DIMENSION_TASK = """
Evaluate all requested dimensions in one pass. Do not produce the final hiring decision.

Return JSON only in this exact shape:
{
  "results": [
    {
      "dimension": "project_analysis",
      "score": 0-10,
      "reasoning": "short evidence-based explanation",
      "evidence": ["specific profile evidence"],
      "confidence": 0-1,
      "red_flags": ["concise red flags"],
      "missing_information": ["what interviewers should verify"],
      "relevance_to_job": "short job-fit note",
      "projects": [],
      "top_project_summary": "",
      "ownership_category": "",
      "inferred_level": ""
    }
  ]
}

Rules:
- Return exactly one result per requested dimension.
- Use scores from 0 to 10.
- 0-2 means little or no credible evidence.
- 3-4 means weak or mostly indirect evidence.
- 5-6 means usable but incomplete evidence.
- 7-8 means strong relevant evidence with minor gaps.
- 9-10 means exceptional, specific, job-aligned evidence.
- Evidence must cite concrete candidate profile details, not generic claims.
- Keep each reasoning concise but useful.
- For project_analysis, include projects and top_project_summary when available.
- For ownership, include ownership_category when inferable.
- For experience_depth, include inferred_level when inferable.

Requested dimensions:
{dimensions}

Job:
{job}

Rubrics:
{rubrics}

Candidate profile:
{candidate_profile}
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_prompt(session: Session, key: str) -> tuple[PromptTemplate | None, PromptTemplateVersion | None]:
    template = session.exec(select(PromptTemplate).where(PromptTemplate.key == key)).first()
    if not template:
        return None, None
    if template.active_version_id:
        version = session.get(PromptTemplateVersion, template.active_version_id)
        if version:
            return template, version
    version = session.exec(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template.id)
        .order_by(PromptTemplateVersion.version.desc())
    ).first()
    return template, version


def _rubric_snapshot(rubrics: list[JobRubric]) -> dict[str, Any]:
    return {
        rubric.dimension: {
            "weight": rubric.weight,
            "instructions": rubric.instructions,
            "low": rubric.low_description,
            "mid": rubric.mid_description,
            "high": rubric.high_description,
            "red_flags": rubric.red_flag_guidance,
            "confidence": rubric.confidence_guidance,
            "version": rubric.version,
            "enabled": rubric.enabled,
        }
        for rubric in rubrics
    }


def _format_job(job: JobProfile, rubrics: list[JobRubric]) -> dict[str, Any]:
    return {
        "title": job.title,
        "department": job.department,
        "role_level": job.role_level,
        "description": job.description,
        "summary": job.summary,
        "success_definition": job.success_definition,
        "responsibilities": job.responsibilities,
        "practical_capabilities": job.practical_capabilities,
        "requirements": job.requirements,
        "thresholds": job.thresholds,
        "rubrics": _rubric_snapshot(rubrics),
    }


def _render_prompt(template: str, **values: Any) -> str:
    rendered = template
    for key, value in values.items():
        if isinstance(value, str):
            replacement = value
        else:
            replacement = json.dumps(value, ensure_ascii=False, default=str)
        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def _candidate_profile_has_evidence(profile_json: dict[str, Any]) -> bool:
    evidence_fields = [
        "candidate_name",
        "headline",
        "education_entries",
        "experience_entries",
        "projects",
        "skills",
        "tools_platforms",
        "inferred_domains",
        "certifications",
        "achievements",
        "possible_seniority_indicators",
        "possible_ownership_indicators",
        "project_evidence_snippets",
    ]
    return any(bool(profile_json.get(field)) for field in evidence_fields)


def _fallback_candidate_profile_from_text(text: str, applicant: Applicant) -> CandidateProfileSchema:
    lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip(" -•\t")]
    candidate_name = applicant.candidate_name or (lines[0] if lines else None)
    skill_terms = [
        "Python",
        "Scikit-learn",
        "Pandas",
        "NumPy",
        "PyTorch",
        "Matplotlib",
        "Seaborn",
        "Regression",
        "Classification",
        "Clustering",
        "Anomaly Detection",
        "Recommendation Systems",
        "Feature Engineering",
        "Model Evaluation",
        "Cross Validation",
        "Hyperparameter Tuning",
        "Node.js",
        "Express.js",
        "REST APIs",
        "MySQL",
        "Postgres",
        "MongoDB",
        "Next Js",
        "Nest Js",
        "React",
        "FastAPI",
        "Docker",
    ]
    lowered = text.lower()
    skills = [skill for skill in skill_terms if skill.lower() in lowered]
    project_keywords = ("system", "segmentation", "detection", "recommendation", "pipeline", "engine")
    projects = []
    for index, line in enumerate(lines):
        is_header = line.upper() == line and len(line.split()) <= 4
        has_project_shape = any(keyword in line.lower() for keyword in project_keywords) and len(line.split()) <= 8
        if has_project_shape and not is_header and "@" not in line and not re.search(r"\+?\d{3}", line):
            details = []
            for detail in lines[index + 1 : index + 4]:
                if detail.upper() == detail or any(keyword in detail.lower() for keyword in project_keywords) and len(detail.split()) <= 8:
                    break
                details.append(detail)
            projects.append({"name": line, "details": details})
    experience_entries = []
    for line in lines:
        if "intern" in line.lower() or "developer" in line.lower():
            experience_entries.append({"title": line})
    domains = []
    if any(skill.lower() in lowered for skill in ["scikit-learn", "pytorch", "machine learning", "regression", "classification"]):
        domains.append("Machine Learning")
    if any(skill.lower() in lowered for skill in ["node.js", "next js", "nest js", "mongodb", "express.js"]):
        domains.append("Web Development")
    evidence = [line for line in lines if any(term.lower() in line.lower() for term in ("built", "developed", "implemented", "evaluated", "applied"))][:8]
    return CandidateProfileSchema(
        candidate_name=candidate_name,
        headline=next((line for line in lines if "engineer" in line.lower() or "developer" in line.lower()), None),
        experience_entries=experience_entries,
        projects=projects[:8],
        skills=skills,
        tools_platforms=skills,
        inferred_domains=domains,
        project_evidence_snippets=evidence,
        possible_ownership_indicators=evidence,
        ambiguity_flags=["Structured profile was built from parser fallback because the LLM profile response was malformed."],
        confidence=0.35,
    )


async def evaluate_applicant(session: Session, applicant_id: UUID, job_id: UUID | None = None) -> EvaluationRun:
    applicant = session.get(Applicant, applicant_id)
    if not applicant:
        raise ValueError("Applicant not found")
    job = session.get(JobProfile, job_id or applicant.job_id)
    if not job:
        raise ValueError("Job profile not found")
    rubrics = session.exec(select(JobRubric).where(JobRubric.job_id == job.id)).all()

    run = EvaluationRun(
        applicant_id=applicant.id,
        job_id=job.id,
        status=EvaluationStatus.running,
        rubric_snapshot=_rubric_snapshot(rubrics),
        started_at=_utc_now(),
    )
    applicant.processing_status = EvaluationStatus.running
    session.add(run)
    session.add(applicant)
    session.commit()
    session.refresh(run)

    try:
        resume = await _ensure_resume_text(session, applicant)
        profile = await _ensure_candidate_profile(session, applicant, resume, job, rubrics)
        dimension_results = await _run_dimensions(session, run, applicant, job, rubrics, profile)
        final = await _run_synthesis(session, run, applicant, job, rubrics, profile, dimension_results)
        _apply_outputs(applicant, final)
        run.status = EvaluationStatus.completed
        run.completed_at = _utc_now()
        applicant.processing_status = EvaluationStatus.completed
        session.add(run)
        session.add(applicant)
        session.commit()
        return run
    except Exception as exc:
        is_missing_resume = isinstance(exc, ResumeParsingError) and "Missing resume_storage_link" in str(exc)
        run.status = EvaluationStatus.missing_resume if is_missing_resume else EvaluationStatus.failed
        run.reason = str(exc)
        run.completed_at = _utc_now()
        applicant.processing_status = run.status
        applicant.system_outputs = {
            **(applicant.system_outputs or {}),
            "resume_analysis_status": "missing_resume" if is_missing_resume else "failed",
            "ai_notes": str(exc),
        }
        session.add(run)
        session.add(applicant)
        session.commit()
        return run


async def _ensure_resume_text(session: Session, applicant: Applicant) -> Resume:
    resume = session.exec(select(Resume).where(Resume.applicant_id == applicant.id)).first()
    if not resume:
        raise ResumeParsingError("No resume record exists for applicant")
    if resume.extracted_text:
        return resume
    if not resume.storage_link:
        applicant.processing_status = EvaluationStatus.missing_resume
        session.add(applicant)
        session.commit()
        raise ResumeParsingError("Missing resume_storage_link")

    resume.extraction_status = "fetching"
    session.add(resume)
    session.commit()
    content, content_type = await fetch_resume_bytes(resume.storage_link)
    text, diagnostics = extract_text_from_bytes(content, resume.file_name, resume.mime_type or content_type)
    resume.extracted_text = text
    resume.mime_type = resume.mime_type or content_type
    resume.extraction_status = "completed"
    resume.parser_diagnostics = diagnostics
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return resume


async def _ensure_candidate_profile(
    session: Session,
    applicant: Applicant,
    resume: Resume,
    job: JobProfile,
    rubrics: list[JobRubric],
) -> CandidateProfile:
    existing = session.exec(select(CandidateProfile).where(CandidateProfile.applicant_id == applicant.id)).first()
    if existing and _candidate_profile_has_evidence(existing.profile_json or {}):
        return existing
    if existing:
        session.delete(existing)
        session.commit()
    _, version = _latest_prompt(session, "candidate_profile")
    if not version:
        raise LLMError("Missing active candidate_profile prompt template")

    settings = get_settings()
    text = (resume.extracted_text or "")[: settings.max_resume_chars_for_llm]
    prompt = _render_prompt(
        version.task_prompt,
        resume_text=text,
        parsed_sections=resume.parsed_sections,
        job=_format_job(job, rubrics),
    )
    model_name = version.model_name
    try:
        result, meta = await DeepSeekClient().json_completion(
            system_prompt=version.system_prompt,
            user_prompt=prompt,
            schema=CandidateProfileSchema,
            model=version.model_name,
            temperature=version.temperature,
            max_tokens=max(version.max_tokens, 6000),
        )
    except LLMError:
        result = _fallback_candidate_profile_from_text(resume.extracted_text or "", applicant)
        meta = {"usage": {}, "raw": {"fallback": "parser_profile"}}
        model_name = "parser-fallback"
    profile = CandidateProfile(
        applicant_id=applicant.id,
        resume_id=resume.id,
        profile_json=result.model_dump(),
        prompt_version_id=version.id,
        model_name=model_name,
        confidence=result.confidence,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


async def _run_dimensions(
    session: Session,
    run: EvaluationRun,
    applicant: Applicant,
    job: JobProfile,
    rubrics: list[JobRubric],
    profile: CandidateProfile,
) -> list[EvaluationDimensionResult]:
    controls = job.prompt_controls or {}
    enabled = controls.get("enabled_passes") or DEFAULT_ENABLED_DIMENSIONS
    if not controls.get("separate_dimension_calls", False):
        return await _run_dimensions_batched(session, run, applicant, job, rubrics, profile, enabled)

    rubric_by_dimension = {rubric.dimension: rubric for rubric in rubrics if rubric.enabled}
    results: list[EvaluationDimensionResult] = []
    for dimension in enabled:
        if dimension not in DIMENSION_SCHEMAS:
            continue
        result = await _run_one_dimension(session, run, applicant, job, rubric_by_dimension.get(dimension), profile, dimension)
        if result is not None:
            results.append(result)
    return results


async def _run_dimensions_batched(
    session: Session,
    run: EvaluationRun,
    applicant: Applicant,
    job: JobProfile,
    rubrics: list[JobRubric],
    profile: CandidateProfile,
    enabled: list[str],
) -> list[EvaluationDimensionResult]:
    dimensions = [dimension for dimension in enabled if dimension in DIMENSION_SCHEMAS]
    rubric_by_dimension = {rubric.dimension: rubric.model_dump() for rubric in rubrics if rubric.enabled}
    _, version = _latest_prompt(session, "project_analysis")
    prompt = _render_prompt(
        BATCH_DIMENSION_TASK,
        dimensions=dimensions,
        job={
            "title": job.title,
            "summary": job.summary,
            "requirements": job.requirements,
            "thresholds": job.thresholds,
        },
        rubrics={dimension: rubric_by_dimension.get(dimension, {}) for dimension in dimensions},
        candidate_profile=profile.profile_json,
    )
    result, meta = await DeepSeekClient().json_completion(
        system_prompt=version.system_prompt if version else BATCH_DIMENSION_SYSTEM_PROMPT,
        user_prompt=prompt,
        schema=BatchDimensionEvaluations,
        model=version.model_name if version else None,
        temperature=version.temperature if version else 0.1,
        max_tokens=max(version.max_tokens if version else 2000, 7000),
    )
    by_dimension = {item.dimension: item for item in result.results}
    stored: list[EvaluationDimensionResult] = []
    for dimension in dimensions:
        item = by_dimension.get(dimension)
        if not item:
            item = BatchDimensionEvaluations.model_validate(
                {
                    "results": [
                        {
                            "dimension": dimension,
                            "score": 0,
                            "reasoning": "The batch dimension response did not include this dimension.",
                            "evidence": [],
                            "confidence": 0.1,
                            "red_flags": ["Missing dimension result from batch evaluation."],
                            "missing_information": ["Re-run analysis or inspect the candidate profile."],
                            "relevance_to_job": "",
                        }
                    ]
                }
            ).results[0]
        dimension_result = EvaluationDimensionResult(
            run_id=run.id,
            applicant_id=applicant.id,
            dimension=dimension,
            score=item.score,
            confidence=item.confidence,
            result_json=item.model_dump(),
            prompt_version_id=version.id if version else None,
            model_name=version.model_name if version else None,
            token_usage=meta.get("usage", {}),
            raw_response=meta.get("raw", {}),
        )
        session.add(dimension_result)
        session.commit()
        session.refresh(dimension_result)
        stored.append(dimension_result)
    return stored


async def _run_one_dimension(
    session: Session,
    run: EvaluationRun,
    applicant: Applicant,
    job: JobProfile,
    rubric: JobRubric | None,
    profile: CandidateProfile,
    dimension: str,
) -> EvaluationDimensionResult | None:
    _, version = _latest_prompt(session, dimension)
    if not version:
        return None
    prompt = _render_prompt(
        version.task_prompt,
        candidate_profile=profile.profile_json,
        job={
            "title": job.title,
            "summary": job.summary,
            "requirements": job.requirements,
            "thresholds": job.thresholds,
        },
        rubric=rubric.model_dump() if rubric else {},
    )
    schema = DIMENSION_SCHEMAS[dimension]
    result, meta = await DeepSeekClient().json_completion(
        system_prompt=version.system_prompt,
        user_prompt=prompt,
        schema=schema,
        model=version.model_name,
        temperature=version.temperature,
        max_tokens=version.max_tokens,
    )
    dimension_result = EvaluationDimensionResult(
        run_id=run.id,
        applicant_id=applicant.id,
        dimension=dimension,
        score=result.score,
        confidence=result.confidence,
        result_json=result.model_dump(),
        prompt_version_id=version.id,
        model_name=version.model_name,
        token_usage=meta.get("usage", {}),
        raw_response=meta.get("raw", {}),
    )
    session.add(dimension_result)
    session.commit()
    session.refresh(dimension_result)
    return dimension_result


async def _run_synthesis(
    session: Session,
    run: EvaluationRun,
    applicant: Applicant,
    job: JobProfile,
    rubrics: list[JobRubric],
    profile: CandidateProfile,
    dimension_results: list[EvaluationDimensionResult],
) -> FinalEvaluation:
    _, version = _latest_prompt(session, "final_synthesis")
    if not version:
        raise LLMError("Missing active final_synthesis prompt template")
    weighted_score = _controlled_weighted_score(dimension_results, rubrics)
    prompt = _render_prompt(
        version.task_prompt,
        candidate_profile=profile.profile_json,
        job=_format_job(job, rubrics),
        dimension_results=[result.result_json for result in dimension_results],
        controlled_weighted_score=weighted_score,
    )
    result, meta = await DeepSeekClient().json_completion(
        system_prompt=version.system_prompt,
        user_prompt=prompt,
        schema=FinalSynthesisSchema,
        model=version.model_name,
        temperature=version.temperature,
        max_tokens=version.max_tokens,
    )
    decision = result.final_candidate_decision.strip().lower()
    if decision not in {Decision.shortlist.value, Decision.review.value, Decision.reject.value}:
        thresholds = job.thresholds or {}
        shortlist = float(thresholds.get("shortlist", 75))
        review = float(thresholds.get("review", 55))
        decision = Decision.shortlist.value if result.final_candidate_score >= shortlist else Decision.review.value if result.final_candidate_score >= review else Decision.reject.value
    final = FinalEvaluation(
        run_id=run.id,
        applicant_id=applicant.id,
        final_score=result.final_candidate_score,
        final_confidence=result.final_candidate_confidence,
        decision=Decision(decision),
        interview_recommendation=result.interview_recommendation,
        summary=result.candidate_fit_summary,
        strengths=result.top_strengths,
        gaps=result.top_gaps,
        best_project_relevance=result.best_project_relevance,
        interview_focus_areas=result.interview_focus_areas,
        red_flags=result.red_flags,
        missing_information=result.missing_information,
        synthesis_json={**result.model_dump(), "controlled_weighted_score": weighted_score, "token_usage": meta.get("usage", {})},
    )
    session.add(final)
    session.commit()
    session.refresh(final)
    return final


def _controlled_weighted_score(results: list[EvaluationDimensionResult], rubrics: list[JobRubric]) -> float:
    weights = {rubric.dimension: rubric.weight for rubric in rubrics if rubric.enabled}
    total_weight = 0.0
    weighted = 0.0
    for result in results:
        if result.score is None:
            continue
        weight = weights.get(result.dimension, 1.0)
        total_weight += weight
        weighted += result.score * 10 * weight
    return round(weighted / total_weight, 2) if total_weight else 0.0


def _apply_outputs(applicant: Applicant, final: FinalEvaluation) -> None:
    applicant.system_outputs = {
        "resume_analysis_status": "completed",
        "final_candidate_score": final.final_score,
        "final_candidate_decision": final.decision.value,
        "candidate_fit_summary": final.summary,
        "top_strengths": final.strengths,
        "top_gaps": final.gaps,
        "best_project_relevance": final.best_project_relevance,
        "interview_recommendation": final.interview_recommendation,
        "interview_focus_areas": final.interview_focus_areas,
        "ai_notes": "; ".join(final.red_flags[:2]) if final.red_flags else "",
    }
