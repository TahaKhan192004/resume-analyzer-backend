from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.entities import Applicant, EvaluationRun, EvaluationStatus


def recover_interrupted_analysis(session: Session) -> dict[str, int]:
    interrupted = session.exec(select(Applicant).where(Applicant.processing_status == EvaluationStatus.running)).all()
    for applicant in interrupted:
        applicant.processing_status = EvaluationStatus.queued
        applicant.system_outputs = {**(applicant.system_outputs or {}), "resume_analysis_status": "queued"}
        session.add(applicant)

    if interrupted:
        interrupted_ids = [applicant.id for applicant in interrupted]
        running_runs = session.exec(
            select(EvaluationRun).where(
                EvaluationRun.applicant_id.in_(interrupted_ids),
                EvaluationRun.status == EvaluationStatus.running,
            )
        ).all()
        for run in running_runs:
            run.status = EvaluationStatus.failed
            run.reason = "Interrupted by backend or worker restart; applicant was requeued."
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)
        session.commit()

    queued = session.exec(select(Applicant).where(Applicant.processing_status == EvaluationStatus.queued)).all()

    from app.workers.tasks import evaluate_applicant_task

    for applicant in queued:
        queued_job_id = (applicant.system_outputs or {}).get("queued_job_id")
        evaluate_applicant_task.delay(str(applicant.id), str(queued_job_id) if queued_job_id else None)

    return {"interrupted_requeued": len(interrupted), "queued_dispatched": len(queued)}
