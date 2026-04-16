import asyncio
from uuid import UUID

from sqlmodel import Session

from app.db.session import engine
from app.models.entities import Applicant, ApplicantImport, EvaluationStatus
from app.services.evaluation_service import evaluate_applicant
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.evaluate_applicant_task")
def evaluate_applicant_task(applicant_id: str, job_id: str | None = None) -> str:
    with Session(engine) as session:
        applicant_uuid = UUID(applicant_id)
        job_uuid = UUID(job_id) if job_id else None
        applicant = session.get(Applicant, applicant_uuid)
        if not applicant or applicant.processing_status != EvaluationStatus.queued:
            return applicant_id
        if applicant.import_id:
            import_record = session.get(ApplicantImport, applicant.import_id)
            if import_record and import_record.status == "paused":
                return applicant_id
        if not job_uuid and (applicant.system_outputs or {}).get("queued_job_id"):
            job_uuid = UUID(str(applicant.system_outputs["queued_job_id"]))
        asyncio.run(evaluate_applicant(session, applicant_uuid, job_uuid))
    return applicant_id
