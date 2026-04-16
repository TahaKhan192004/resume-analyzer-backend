from sqlmodel import Session, select

from app.core.security import hash_password
from app.db.session import engine, init_db
from app.models.entities import JobProfile, JobRubric, PromptTemplate, PromptTemplateVersion, User

DIMENSIONS = [
    ("project_analysis", "Evaluate project relevance, maturity, and seriousness of work."),
    ("project_complexity", "Evaluate technical, system, domain, integration, deployment, and problem-solving complexity."),
    ("ownership", "Evaluate what the candidate likely owned: assistant, contributor, builder, lead, architect, or owner."),
    ("skill_relevance", "Evaluate essential and desirable skills with practical depth, not mere mentions."),
    ("experience_depth", "Evaluate role maturity, progression, practical depth, and level fit."),
    ("education_relevance", "Evaluate education as helpful, neutral, or weakly relevant without over-penalizing strong practical evidence."),
    ("communication_clarity", "Evaluate resume clarity, evidence quality, and professionalism."),
    ("growth_potential", "Evaluate initiative and learning potential, especially for internship and junior roles."),
]

SYSTEM_JSON = (
    "You are a rigorous senior recruiter and technical evaluator. Return JSON only and follow the requested schema exactly. "
    "This is not keyword matching. Evaluate evidence quality, project maturity, candidate ownership, practical skill depth, "
    "role relevance, and uncertainty. Do not invent facts. Separate strong evidence from weak inference. If the resume is vague, "
    "say so and lower confidence. Always include reasoning, evidence, missing information, red flags, and relevance to the job."
)

PROFILE_TASK = """
Transform this resume into a normalized candidate profile JSON matching the requested schema.

Rules:
- Do not score the candidate in this pass.
- Extract facts, evidence snippets, and ambiguity flags.
- Preserve project names, technologies, outcomes, deployment signals, team/solo indicators, and candidate role wording.
- Separate explicit facts from reasonable inferences.
- If data is missing, leave the field empty and add an ambiguity flag.
- Use the job only as context for what evidence may matter later.

Job context:
{job}

Parsed sections:
{parsed_sections}

Resume text:
{resume_text}
"""

DIMENSION_TASK = """
Evaluate only this dimension using the rubric. Do not produce a final hiring decision.

Scoring rules:
- Score 0 to 10.
- 0-2 means little or no credible evidence.
- 3-4 means weak or mostly indirect evidence.
- 5-6 means usable but incomplete evidence.
- 7-8 means strong relevant evidence with minor gaps.
- 9-10 means exceptional, specific, job-aligned evidence.
- Confidence must be 0 to 1 and should drop when the resume is vague or evidence is indirect.
- Evidence must cite concrete candidate profile details, not generic claims.
- Red flags should be concise and evidence-aware.
- Missing information should describe what an interviewer should verify.

Job:
{job}

Rubric:
{rubric}

Candidate profile:
{candidate_profile}
"""

SYNTHESIS_TASK = """
Create the final synthesis from the specialized dimension outputs. Do not re-evaluate from raw resume text.
Use the controlled weighted score as an anchor, and adjust only when the dimension evidence strongly supports it.

Decision rules:
- shortlist: strong overall evidence for the role and final score generally at or above the shortlist threshold.
- review: promising but incomplete, mixed, or uncertain evidence.
- reject: insufficient alignment, major gaps, or weak evidence for the role.
- Do not reject solely for education if projects, skills, and ownership are strong unless the job requires it.
- Keep the recruiter summary short, practical, and interview-oriented.
- Use interview focus areas to identify what a human should verify next.

Job and thresholds:
{job}

Candidate profile:
{candidate_profile}

Dimension results:
{dimension_results}

Controlled weighted score:
{controlled_weighted_score}
"""


def upsert_prompt(session: Session, key: str, name: str, task_prompt: str) -> None:
    template = session.exec(select(PromptTemplate).where(PromptTemplate.key == key)).first()
    if template:
        active = session.get(PromptTemplateVersion, template.active_version_id) if template.active_version_id else None
        if active and active.system_prompt == SYSTEM_JSON and active.task_prompt == task_prompt:
            return
        latest = session.exec(
            select(PromptTemplateVersion)
            .where(PromptTemplateVersion.template_id == template.id)
            .order_by(PromptTemplateVersion.version.desc())
        ).first()
        next_version = (latest.version + 1) if latest else 1
    else:
        template = PromptTemplate(key=key, name=name, description=f"Code-managed template for {name}")
        session.add(template)
        session.flush()
        next_version = 1
    version = PromptTemplateVersion(
        template_id=template.id,
        version=next_version,
        system_prompt=SYSTEM_JSON,
        task_prompt=task_prompt,
        output_schema={"format": "json_object"},
        model_name="deepseek-chat",
        temperature=0.1,
        max_tokens=2400 if key == "final_synthesis" else 2000,
    )
    session.add(version)
    session.flush()
    template.active_version_id = version.id
    session.add(template)


def run() -> None:
    init_db()
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == "admin@example.com")).first()
        if not user:
            session.add(User(email="admin@example.com", full_name="Admin User", password_hash=hash_password("admin123")))

        job = session.exec(select(JobProfile).where(JobProfile.title == "AI Engineer Intern")).first()
        if not job:
            job = JobProfile(
                title="AI Engineer Intern",
                department="Engineering",
                employment_type="Internship",
                role_level="Intern",
                location="Remote / Hybrid",
                status="active",
                description="Help build AI-assisted internal products using Python, APIs, data pipelines, and practical model integrations.",
                summary="Practical junior AI engineering role focused on projects, implementation ability, and learning velocity.",
                success_definition="Can turn ambiguous product needs into working, maintainable AI-backed features with supervision.",
                responsibilities="Build FastAPI services, integrate LLM APIs, process documents, write reliable scripts, and communicate tradeoffs.",
                practical_capabilities="Python, APIs, data handling, prompt design, basic deployment, debugging, and project ownership.",
                requirements={
                    "essential_skills": ["Python", "API integration", "LLM prompting", "data processing", "Git"],
                    "desirable_skills": ["FastAPI", "PostgreSQL", "React", "Docker", "OCR/document parsing"],
                    "preferred_projects": ["LLM apps", "document automation", "search/retrieval", "production-like web apps"],
                    "preferred_ownership_level": "Builder or strong contributor",
                    "expected_experience_depth": "Intern/junior with evidence of shipped or working projects",
                },
                thresholds={"shortlist": 75, "review": 55, "reject": 0},
                prompt_controls={"enabled_passes": [dimension for dimension, _ in DIMENSIONS]},
            )
            session.add(job)
            session.flush()
            weights = {
                "project_analysis": 1.3,
                "project_complexity": 1.1,
                "ownership": 1.2,
                "skill_relevance": 1.4,
                "experience_depth": 1.0,
                "education_relevance": 0.6,
                "communication_clarity": 0.6,
                "growth_potential": 0.9,
            }
            for dimension, instructions in DIMENSIONS:
                session.add(
                    JobRubric(
                        job_id=job.id,
                        dimension=dimension,
                        weight=weights.get(dimension, 1.0),
                        instructions=instructions,
                        low_description="Little relevant evidence or mostly vague claims.",
                        mid_description="Some relevant evidence with gaps or limited practical depth.",
                        high_description="Strong, specific evidence aligned with role expectations.",
                        red_flag_guidance="Flag unsupported claims, vague project ownership, irrelevant stack, or missing critical evidence.",
                        confidence_guidance="Lower confidence when evidence is sparse, ambiguous, or resume text is incomplete.",
                    )
                )

        upsert_prompt(session, "candidate_profile", "Candidate Profile Generation", PROFILE_TASK)
        for dimension, _ in DIMENSIONS:
            upsert_prompt(session, dimension, dimension.replace("_", " ").title(), DIMENSION_TASK)
        upsert_prompt(session, "final_synthesis", "Final Synthesis", SYNTHESIS_TASK)
        session.commit()
        print("Seed complete. Login with admin@example.com / admin123")


if __name__ == "__main__":
    run()
