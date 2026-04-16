from uuid import UUID

from sqlmodel import Session, select

from app.models.entities import (
    Applicant,
    ApplicantImport,
    CandidateProfile,
    EvaluationDimensionResult,
    EvaluationRun,
    FinalEvaluation,
    Resume,
)


def _delete_rows_for_applicant(session: Session, model: type, applicant_id: UUID) -> None:
    rows = session.exec(select(model).where(model.applicant_id == applicant_id)).all()
    for row in rows:
        session.delete(row)


def delete_applicant_tree(session: Session, applicant_id: UUID) -> bool:
    applicant = session.get(Applicant, applicant_id)
    if not applicant:
        return False

    _delete_rows_for_applicant(session, FinalEvaluation, applicant_id)
    _delete_rows_for_applicant(session, EvaluationDimensionResult, applicant_id)
    session.flush()

    _delete_rows_for_applicant(session, CandidateProfile, applicant_id)
    session.flush()

    _delete_rows_for_applicant(session, EvaluationRun, applicant_id)
    _delete_rows_for_applicant(session, Resume, applicant_id)
    session.flush()

    session.delete(applicant)
    session.flush()
    return True


def clear_applicant_analysis(session: Session, applicant_id: UUID) -> None:
    _delete_rows_for_applicant(session, FinalEvaluation, applicant_id)
    _delete_rows_for_applicant(session, EvaluationDimensionResult, applicant_id)
    session.flush()

    _delete_rows_for_applicant(session, CandidateProfile, applicant_id)
    session.flush()

    _delete_rows_for_applicant(session, EvaluationRun, applicant_id)
    session.flush()


def clear_applicant_job_analysis(session: Session, applicant_id: UUID, job_id: UUID) -> None:
    runs = session.exec(select(EvaluationRun).where(EvaluationRun.applicant_id == applicant_id, EvaluationRun.job_id == job_id)).all()
    run_ids = [run.id for run in runs]
    for run_id in run_ids:
        rows = session.exec(select(FinalEvaluation).where(FinalEvaluation.run_id == run_id)).all()
        for row in rows:
            session.delete(row)
        rows = session.exec(select(EvaluationDimensionResult).where(EvaluationDimensionResult.run_id == run_id)).all()
        for row in rows:
            session.delete(row)
    session.flush()
    for run in runs:
        session.delete(run)
    session.flush()


def delete_import_tree(session: Session, import_id: UUID) -> bool:
    import_record = session.get(ApplicantImport, import_id)
    if not import_record:
        return False
    with session.no_autoflush:
        applicant_ids = [applicant.id for applicant in session.exec(select(Applicant).where(Applicant.import_id == import_id)).all()]
    for applicant_id in applicant_ids:
        delete_applicant_tree(session, applicant_id)
    session.delete(import_record)
    session.flush()
    return True
