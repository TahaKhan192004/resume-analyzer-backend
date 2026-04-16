from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.entities import JobProfile, JobRubric, User
from app.schemas.contracts import JobProfileDraftRequest, JobProfileDraftSchema, JobProfilePayload
from app.services.llm_client import DeepSeekClient

router = APIRouter(prefix="/jobs", tags=["jobs"])

DEFAULT_RUBRICS = [
    {
        "dimension": "project_analysis",
        "weight": 1.3,
        "instructions": "Evaluate project relevance, maturity, real-world usefulness, and how strongly the projects indicate capability for the target role.",
    },
    {
        "dimension": "project_complexity",
        "weight": 1.1,
        "instructions": "Evaluate technical complexity, architecture, integrations, deployment indicators, scale, and problem-solving depth.",
    },
    {
        "dimension": "ownership",
        "weight": 1.2,
        "instructions": "Infer whether the candidate was an assistant, contributor, builder, lead, architect, or owner. Penalize vague involvement.",
    },
    {
        "dimension": "skill_relevance",
        "weight": 1.4,
        "instructions": "Evaluate practical skill relevance to the job. Distinguish demonstrated depth from mere keyword mentions.",
    },
    {
        "dimension": "experience_depth",
        "weight": 1.0,
        "instructions": "Evaluate quality, maturity, progression, and practical depth of experience for the target role level.",
    },
    {
        "dimension": "education_relevance",
        "weight": 0.6,
        "instructions": "Evaluate education as supportive, neutral, or weakly relevant. Do not over-penalize strong practical evidence.",
    },
    {
        "dimension": "communication_clarity",
        "weight": 0.6,
        "instructions": "Evaluate resume clarity, specificity of evidence, professionalism, and how easy it is to understand the candidate's work.",
    },
    {
        "dimension": "growth_potential",
        "weight": 0.9,
        "instructions": "For internship and junior roles, evaluate initiative, learning velocity, self-driven projects, and ambition relative to stage.",
    },
]


def _create_default_rubrics(session: Session, job_id: UUID) -> None:
    for item in DEFAULT_RUBRICS:
        session.add(
            JobRubric(
                job_id=job_id,
                dimension=item["dimension"],
                weight=item["weight"],
                instructions=item["instructions"],
                low_description="Little relevant evidence, vague claims, or weak alignment with the role expectations.",
                mid_description="Some relevant evidence, but with gaps in depth, ownership, maturity, or job alignment.",
                high_description="Strong specific evidence that aligns with the role expectations and shows practical capability.",
                red_flag_guidance="Flag unsupported claims, vague project ownership, missing critical skills, shallow projects, or unclear resume evidence.",
                confidence_guidance="Lower confidence when resume evidence is sparse, ambiguous, incomplete, or contradicted.",
                enabled=True,
            )
        )


@router.get("")
def list_jobs(session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    jobs = session.exec(select(JobProfile).order_by(JobProfile.updated_at.desc())).all()
    return jobs


@router.post("/draft-from-description")
async def draft_job_from_description(payload: JobProfileDraftRequest, _: User = Depends(get_current_user)):
    prompt = f"""
Create an editable recruiter-facing job profile from the raw job description.

Rules:
- Keep it simple for an admin user.
- Do not create evaluation rubrics or prompt settings.
- Extract likely title, department, employment type, role level, and location when present.
- Convert responsibilities and requirements into clear fields.
- Prefer practical capability expectations over years of experience.
- If the description says experience is not required, preserve that clearly.
- Return JSON only.

Raw job description:
{payload.description}
"""
    try:
        result, _ = await DeepSeekClient().json_completion(
            system_prompt="You create clean job profile drafts for a recruiter dashboard. Return JSON only.",
            user_prompt=prompt,
            schema=JobProfileDraftSchema,
            temperature=0.1,
            max_tokens=1800,
        )
        return result
    except Exception:
        return _fallback_job_draft(payload.description)


def _fallback_job_draft(description: str) -> JobProfileDraftSchema:
    lines = [line.strip(" •\t") for line in description.splitlines() if line.strip(" •\t")]
    title = lines[0] if lines else "New Job Profile"
    text = description.lower()
    return JobProfileDraftSchema(
        title=title[:120],
        department="Engineering" if "engineer" in text or "developer" in text else "",
        employment_type="Internship" if "intern" in text else "",
        role_level="Intern / Junior" if "intern" in text or "junior" in text else "",
        location="Lahore" if "lahore" in text else "",
        summary=description[:500],
        description=description,
        success_definition="A strong candidate can learn quickly, explain their projects clearly, and show practical hands-on work aligned with this role.",
        responsibilities="\n".join(line for line in lines if any(word in line.lower() for word in ["build", "assist", "integrate", "develop", "collaborate", "document", "test"])),
        practical_capabilities="Practical project work, API understanding, problem solving, debugging, and willingness to learn quickly.",
        essential_skills=["Python", "JavaScript", "APIs", "problem solving"] if "python" in text or "javascript" in text else [],
        desirable_skills=[skill for skill in ["n8n", "OpenAI", "Claude", "LangChain", "REST APIs", "webhooks"] if skill.lower() in text],
        tools_platforms=[tool for tool in ["n8n", "OpenAI", "Claude", "LangChain", "Make", "Zapier"] if tool.lower() in text],
        preferred_projects=["Self-built projects", "AI automation projects", "API integration projects"],
        preferred_ownership_level="Self-driven builder or strong contributor",
        expected_experience_depth="No professional experience required. Strong self-projects and practical skills matter more.",
        education_preferences="Junior or early-semester students are acceptable if skills and projects are strong.",
        communication_expectations="Candidate should be able to clearly explain their projects, tools used, and personal contribution.",
    )


@router.post("")
def create_job(payload: JobProfilePayload, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    data = payload.model_dump(exclude={"rubrics"})
    job = JobProfile(**data)
    session.add(job)
    session.flush()
    if payload.rubrics:
        for rubric in payload.rubrics:
            session.add(JobRubric(job_id=job.id, **rubric.model_dump()))
    else:
        _create_default_rubrics(session, job.id)
    session.commit()
    session.refresh(job)
    return job


@router.get("/{job_id}")
def get_job(job_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    job = session.get(JobProfile, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rubrics = session.exec(select(JobRubric).where(JobRubric.job_id == job.id)).all()
    return {**job.model_dump(), "rubrics": [rubric.model_dump() for rubric in rubrics]}


@router.put("/{job_id}")
def update_job(
    job_id: UUID,
    payload: JobProfilePayload,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    job = session.get(JobProfile, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    for key, value in payload.model_dump(exclude={"rubrics"}).items():
        setattr(job, key, value)
    existing = session.exec(select(JobRubric).where(JobRubric.job_id == job.id)).all()
    for rubric in existing:
        session.delete(rubric)
    if payload.rubrics:
        for rubric in payload.rubrics:
            session.add(JobRubric(job_id=job.id, **rubric.model_dump()))
    else:
        _create_default_rubrics(session, job.id)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/duplicate")
def duplicate_job(job_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    job = session.get(JobProfile, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rubrics = session.exec(select(JobRubric).where(JobRubric.job_id == job.id)).all()
    clone_data = job.model_dump(exclude={"id", "created_at", "updated_at"})
    clone_data["title"] = f"{job.title} Copy"
    clone_data["status"] = "draft"
    clone = JobProfile(**clone_data)
    session.add(clone)
    session.flush()
    for rubric in rubrics:
        session.add(JobRubric(job_id=clone.id, **rubric.model_dump(exclude={"id", "job_id", "created_at", "updated_at"})))
    session.commit()
    session.refresh(clone)
    return clone


@router.post("/{job_id}/archive")
def archive_job(job_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    job = session.get(JobProfile, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "archived"
    session.add(job)
    session.commit()
    return job


@router.delete("/{job_id}")
def delete_job(job_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    job = session.get(JobProfile, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    rubrics = session.exec(select(JobRubric).where(JobRubric.job_id == job.id)).all()
    for rubric in rubrics:
        session.delete(rubric)
    session.delete(job)
    session.commit()
    return {"deleted": True}
