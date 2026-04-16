from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.entities import Applicant, ApplicantImport, EvaluationStatus, JobProfile, User
from app.services.csv_service import import_applicant_csv
from app.services.deletion_service import delete_import_tree
from app.workers.tasks import evaluate_applicant_task

router = APIRouter(prefix="/imports", tags=["imports"])


def _status_value(status: object) -> str:
    return getattr(status, "value", str(status))


@router.get("")
def list_imports(session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    imports = session.exec(select(ApplicantImport).order_by(ApplicantImport.created_at.desc())).all()
    results = []
    for import_record in imports:
        applicants = session.exec(select(Applicant).where(Applicant.import_id == import_record.id)).all()
        job = session.get(JobProfile, import_record.job_id)
        counts: dict[str, int] = {}
        for applicant in applicants:
            status = _status_value(applicant.processing_status)
            counts[status] = counts.get(status, 0) + 1
        results.append({**import_record.model_dump(), "job_title": job.title if job else "", "counts": counts, "applicant_count": len(applicants)})
    return results


@router.post("")
async def upload_import(
    job_id: UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    data = await file.read()
    record, applicant_ids = import_applicant_csv(session, data=data, file_name=file.filename or "applicants.csv", job_id=job_id)
    for applicant_id in applicant_ids:
        applicant = session.get(Applicant, applicant_id)
        if applicant:
            applicant.system_outputs = {**(applicant.system_outputs or {}), "queued_job_id": str(job_id)}
            session.add(applicant)
    session.commit()
    task_ids = [evaluate_applicant_task.delay(str(applicant_id), str(job_id)).id for applicant_id in applicant_ids]
    return {**record.model_dump(), "queued_applicant_ids": applicant_ids, "task_ids": task_ids}


@router.get("/{import_id}/progress")
def import_progress(
    import_id: UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    applicants = session.exec(select(Applicant).where(Applicant.import_id == import_id)).all()
    import_record = session.get(ApplicantImport, import_id)
    job = session.get(JobProfile, import_record.job_id) if import_record else None
    counts: dict[str, int] = {}
    for applicant in applicants:
        status = _status_value(applicant.processing_status)
        counts[status] = counts.get(status, 0) + 1
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    missing_resume = counts.get("missing_resume", 0)
    total = len(applicants)
    done = completed + failed + missing_resume
    return {
        "import_id": import_id,
        "status": import_record.status if import_record else "",
        "job_title": job.title if job else "",
        "total": total,
        "done": done,
        "counts": counts,
        "percent": round((done / total) * 100, 1) if total else 0,
        "applicants": [
            {
                "id": applicant.id,
                "candidate_name": applicant.candidate_name,
                "processing_status": applicant.processing_status,
                "decision": (applicant.system_outputs or {}).get("final_candidate_decision"),
                "score": (applicant.system_outputs or {}).get("final_candidate_score"),
            }
            for applicant in applicants
        ],
    }


@router.post("/{import_id}/pause")
def pause_import(
    import_id: UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    import_record = session.get(ApplicantImport, import_id)
    if not import_record:
        raise HTTPException(status_code=404, detail="Import not found")
    import_record.status = "paused"
    session.add(import_record)
    session.commit()
    return {"status": "paused"}


@router.post("/{import_id}/resume")
def resume_import(
    import_id: UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    import_record = session.get(ApplicantImport, import_id)
    if not import_record:
        raise HTTPException(status_code=404, detail="Import not found")
    import_record.status = "imported"
    session.add(import_record)
    applicants = session.exec(select(Applicant).where(Applicant.import_id == import_id, Applicant.processing_status == EvaluationStatus.queued)).all()
    session.commit()
    task_ids = [evaluate_applicant_task.delay(str(applicant.id), (applicant.system_outputs or {}).get("queued_job_id")).id for applicant in applicants]
    return {"status": "imported", "queued": len(task_ids), "task_ids": task_ids}


@router.delete("/{import_id}")
def delete_import(
    import_id: UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    deleted = delete_import_tree(session, import_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Import not found")
    session.commit()
    return {"deleted": True}
