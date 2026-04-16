from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.entities import Applicant, CandidateProfile, EvaluationDimensionResult, EvaluationRun, FinalEvaluation, JobProfile, Resume, User
from app.services.deletion_service import clear_applicant_analysis, clear_applicant_job_analysis, delete_applicant_tree
from app.services.role_matching import applicant_matches_job
from app.workers.tasks import evaluate_applicant_task

router = APIRouter(prefix="/applicants", tags=["applicants"])


class BatchReprocessRequest(BaseModel):
    applicant_ids: list[UUID] | None = None
    job_id: UUID | None = None


class BatchDeleteRequest(BaseModel):
    applicant_ids: list[UUID]


class AnalyzeForJobRequest(BaseModel):
    applicant_ids: list[UUID] | None = None
    import_id: UUID | None = None
    source_job_id: UUID | None = None
    job_id: UUID
    force: bool = False


def _job_analysis_summaries(session: Session, applicant_id: UUID) -> list[dict]:
    applicant = session.get(Applicant, applicant_id)
    runs = session.exec(select(EvaluationRun).where(EvaluationRun.applicant_id == applicant_id).order_by(EvaluationRun.started_at.desc())).all()
    latest_by_job: dict[UUID, EvaluationRun] = {}
    for run in runs:
        if run.job_id not in latest_by_job:
            latest_by_job[run.job_id] = run
    summaries = []
    for job_id, run in latest_by_job.items():
        job = session.get(JobProfile, job_id)
        final = session.exec(select(FinalEvaluation).where(FinalEvaluation.run_id == run.id).order_by(FinalEvaluation.created_at.desc())).first()
        matches_role = False
        role_reason = "Applicant or job record was not found."
        if applicant and job:
            matches_role, role_reason = applicant_matches_job(applicant, job)
        summaries.append(
            {
                "job_id": job_id,
                "job_title": job.title if job else "Unknown job",
                "run_id": run.id,
                "status": run.status.value,
                "reason": run.reason,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "final_score": final.final_score if final else None,
                "decision": final.decision.value if final else None,
                "summary": final.summary if final else "",
                "matches_applied_role": matches_role,
                "role_match_reason": role_reason,
            }
        )
    return summaries


@router.get("")
def list_applicants(
    job_id: UUID | None = None,
    decision: str | None = None,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    applicants = session.exec(select(Applicant).order_by(Applicant.updated_at.desc())).all()
    enriched = []
    for applicant in applicants:
        analyses = _job_analysis_summaries(session, applicant.id)
        job = session.get(JobProfile, applicant.job_id)
        selected_analysis = next((analysis for analysis in analyses if str(analysis["job_id"]) == str(job_id)), None) if job_id else None
        if job_id and not selected_analysis and applicant.job_id != job_id:
            continue
        data = applicant.model_dump()
        data["job_title"] = job.title if job else ""
        data["job_analyses"] = analyses
        if selected_analysis:
            data["selected_job_analysis"] = selected_analysis
        enriched.append(data)
    if decision:
        enriched = [
            item
            for item in enriched
            if (item.get("selected_job_analysis") or {}).get("decision") == decision
            or (item.get("system_outputs") or {}).get("final_candidate_decision") == decision
        ]
    return enriched


@router.post("/reprocess")
def reprocess_batch(payload: BatchReprocessRequest, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    if payload.applicant_ids:
        ids = payload.applicant_ids
    elif payload.job_id:
        ids = [item.id for item in session.exec(select(Applicant).where(Applicant.job_id == payload.job_id)).all()]
    else:
        raise HTTPException(status_code=400, detail="Provide applicant_ids or job_id")
    for applicant_id in ids:
        applicant = session.get(Applicant, applicant_id)
        if applicant:
            clear_applicant_analysis(session, applicant_id)
            applicant.processing_status = "queued"
            applicant.system_outputs = {**(applicant.system_outputs or {}), "resume_analysis_status": "queued"}
            session.add(applicant)
    session.commit()
    tasks = [evaluate_applicant_task.delay(str(applicant_id)).id for applicant_id in ids]
    return {"queued": len(tasks), "task_ids": tasks}


@router.post("/analyze-for-job")
def analyze_for_job(payload: AnalyzeForJobRequest, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    job = session.get(JobProfile, payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if payload.applicant_ids:
        ids = payload.applicant_ids
    elif payload.import_id:
        ids = [item.id for item in session.exec(select(Applicant).where(Applicant.import_id == payload.import_id)).all()]
    elif payload.source_job_id:
        ids = [item.id for item in session.exec(select(Applicant).where(Applicant.job_id == payload.source_job_id)).all()]
    else:
        raise HTTPException(status_code=400, detail="Provide applicant_ids, import_id, or source_job_id")

    queued_ids = []
    skipped = []
    for applicant_id in ids:
        applicant = session.get(Applicant, applicant_id)
        if not applicant:
            skipped.append({"applicant_id": str(applicant_id), "reason": "Applicant not found."})
            continue
        matches_role, reason = applicant_matches_job(applicant, job)
        if not matches_role:
            skipped.append({"applicant_id": str(applicant.id), "candidate_name": applicant.candidate_name, "reason": reason})
            continue
        existing = session.exec(select(EvaluationRun).where(EvaluationRun.applicant_id == applicant.id, EvaluationRun.job_id == payload.job_id)).first()
        if existing and not payload.force:
            skipped.append({"applicant_id": str(applicant.id), "candidate_name": applicant.candidate_name, "reason": "Analysis already exists for the applied role."})
            continue
        if payload.force:
            clear_applicant_job_analysis(session, applicant.id, payload.job_id)
        applicant.processing_status = "queued"
        applicant.system_outputs = {**(applicant.system_outputs or {}), "resume_analysis_status": "queued", "queued_job_id": str(payload.job_id)}
        session.add(applicant)
        queued_ids.append(applicant.id)
    session.commit()
    tasks = [evaluate_applicant_task.delay(str(applicant_id), str(payload.job_id)).id for applicant_id in queued_ids]
    return {"queued": len(tasks), "task_ids": tasks, "skipped": skipped}


@router.post("/delete")
def delete_applicants_batch(payload: BatchDeleteRequest, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    deleted = 0
    for applicant_id in payload.applicant_ids:
        if delete_applicant_tree(session, applicant_id):
            deleted += 1
    session.commit()
    return {"deleted": deleted}


@router.get("/{applicant_id}")
def get_applicant(
    applicant_id: UUID,
    job_id: UUID | None = None,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    applicant = session.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    resume = session.exec(select(Resume).where(Resume.applicant_id == applicant.id)).first()
    profile = session.exec(select(CandidateProfile).where(CandidateProfile.applicant_id == applicant.id).order_by(CandidateProfile.created_at.desc())).first()
    if job_id:
        latest_run = session.exec(
            select(EvaluationRun)
            .where(EvaluationRun.applicant_id == applicant.id, EvaluationRun.job_id == job_id)
            .order_by(EvaluationRun.started_at.desc())
        ).first()
    else:
        latest_run = session.exec(select(EvaluationRun).where(EvaluationRun.applicant_id == applicant.id).order_by(EvaluationRun.started_at.desc())).first()
    dimensions = []
    final = None
    if latest_run:
        dimensions = session.exec(
            select(EvaluationDimensionResult).where(EvaluationDimensionResult.run_id == latest_run.id).order_by(EvaluationDimensionResult.created_at.desc())
        ).all()
        final = session.exec(select(FinalEvaluation).where(FinalEvaluation.run_id == latest_run.id).order_by(FinalEvaluation.created_at.desc())).first()
    analyses = _job_analysis_summaries(session, applicant.id)
    selected_analysis = next((analysis for analysis in analyses if latest_run and analysis["run_id"] == latest_run.id), None)
    return {
        **applicant.model_dump(),
        "job_title": (session.get(JobProfile, applicant.job_id).title if session.get(JobProfile, applicant.job_id) else ""),
        "job_analyses": analyses,
        "selected_job_analysis": selected_analysis,
        "resume": resume.model_dump() if resume else None,
        "profile": profile.model_dump() if profile else None,
        "dimension_results": [item.model_dump() for item in dimensions],
        "final_evaluation": final.model_dump() if final else None,
    }


@router.post("/{applicant_id}/reprocess")
def reprocess_applicant(applicant_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    applicant = session.get(Applicant, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    clear_applicant_analysis(session, applicant_id)
    applicant.processing_status = "queued"
    applicant.system_outputs = {**(applicant.system_outputs or {}), "resume_analysis_status": "queued"}
    session.add(applicant)
    session.commit()
    task = evaluate_applicant_task.delay(str(applicant_id))
    return {"task_id": task.id, "status": "queued"}


@router.delete("/{applicant_id}")
def delete_applicant(applicant_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    deleted = delete_applicant_tree(session, applicant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Applicant not found")
    session.commit()
    return {"deleted": True}
