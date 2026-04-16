from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_session
from app.models.entities import Applicant, CandidateEmail, CandidateEmailStatus, JobProfile, User
from app.services.candidate_email_service import (
    CandidateEmailError,
    latest_rejected_completed_analysis,
    render_rejection_email,
    send_candidate_email,
    utc_now,
)
from app.services.role_matching import applicant_matches_job

router = APIRouter(prefix="/candidate-emails", tags=["candidate-emails"])


class DraftRejectionEmailsRequest(BaseModel):
    applicant_ids: list[UUID] = Field(min_length=1)
    job_id: UUID
    overwrite_existing_drafts: bool = False


class UpdateCandidateEmailRequest(BaseModel):
    subject: str | None = None
    body: str | None = None


class SendCandidateEmailsRequest(BaseModel):
    email_ids: list[UUID] = Field(min_length=1)


def _email_payload(email: CandidateEmail, session: Session) -> dict:
    applicant = session.get(Applicant, email.applicant_id)
    job = session.get(JobProfile, email.job_id)
    return {
        **email.model_dump(),
        "candidate_name": applicant.candidate_name if applicant else "",
        "job_title": job.title if job else "",
    }


@router.get("")
def list_candidate_emails(
    job_id: UUID | None = None,
    applicant_id: UUID | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    statement = select(CandidateEmail).order_by(CandidateEmail.updated_at.desc())
    emails = session.exec(statement).all()
    if job_id:
        emails = [email for email in emails if email.job_id == job_id]
    if applicant_id:
        emails = [email for email in emails if email.applicant_id == applicant_id]
    if status:
        emails = [email for email in emails if email.status.value == status]
    return [_email_payload(email, session) for email in emails]


@router.post("/draft-rejections")
async def draft_rejection_emails(
    payload: DraftRejectionEmailsRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    job = session.get(JobProfile, payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    settings = get_settings()
    from_email = settings.recruiter_from_email or settings.smtp_username
    if not from_email:
        raise HTTPException(status_code=400, detail="Configure RECRUITER_FROM_EMAIL or SMTP_USERNAME before drafting emails.")

    drafted = []
    skipped = []
    for applicant_id in payload.applicant_ids:
        applicant = session.get(Applicant, applicant_id)
        if not applicant:
            skipped.append({"applicant_id": str(applicant_id), "reason": "Applicant not found."})
            continue
        if not applicant.candidate_email:
            skipped.append({"applicant_id": str(applicant_id), "reason": "Candidate email is missing."})
            continue
        matches_role, role_reason = applicant_matches_job(applicant, job)
        if not matches_role:
            skipped.append({"applicant_id": str(applicant_id), "reason": role_reason})
            continue
        try:
            run, final = latest_rejected_completed_analysis(session, applicant.id, job.id)
        except CandidateEmailError as exc:
            skipped.append({"applicant_id": str(applicant_id), "reason": str(exc)})
            continue

        existing = session.exec(
            select(CandidateEmail).where(
                CandidateEmail.applicant_id == applicant.id,
                CandidateEmail.job_id == job.id,
                CandidateEmail.status == CandidateEmailStatus.draft,
            )
        ).first()
        if existing and not payload.overwrite_existing_drafts:
            drafted.append(existing)
            continue
        try:
            subject, body = await render_rejection_email(session, applicant, job, run, final)
        except CandidateEmailError as exc:
            skipped.append({"applicant_id": str(applicant_id), "reason": str(exc)})
            continue
        if existing:
            existing.run_id = run.id
            existing.final_evaluation_id = final.id
            existing.to_email = applicant.candidate_email
            existing.from_email = from_email
            existing.subject = subject
            existing.body = body
            existing.failure_reason = None
            existing.updated_at = utc_now()
            email = existing
        else:
            email = CandidateEmail(
                applicant_id=applicant.id,
                job_id=job.id,
                run_id=run.id,
                final_evaluation_id=final.id,
                to_email=applicant.candidate_email,
                from_email=from_email,
                subject=subject,
                body=body,
                created_by=user.id,
            )
        session.add(email)
        session.commit()
        session.refresh(email)
        drafted.append(email)
    return {"drafted": [_email_payload(email, session) for email in drafted], "skipped": skipped}


@router.patch("/{email_id}")
def update_candidate_email(
    email_id: UUID,
    payload: UpdateCandidateEmailRequest,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    email = session.get(CandidateEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email draft not found")
    if email.status != CandidateEmailStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft emails can be edited.")
    if payload.subject is not None:
        email.subject = payload.subject
    if payload.body is not None:
        email.body = payload.body
    email.updated_at = utc_now()
    session.add(email)
    session.commit()
    session.refresh(email)
    return _email_payload(email, session)


@router.post("/{email_id}/send")
def send_one_candidate_email(email_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    email = session.get(CandidateEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email draft not found")
    if email.status != CandidateEmailStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft emails can be sent.")
    try:
        send_candidate_email(email)
    except CandidateEmailError as exc:
        email.status = CandidateEmailStatus.failed
        email.failure_reason = str(exc)
    except Exception as exc:
        email.status = CandidateEmailStatus.failed
        email.failure_reason = f"SMTP send failed: {exc}"
    email.updated_at = utc_now()
    session.add(email)
    session.commit()
    session.refresh(email)
    if email.status == CandidateEmailStatus.failed:
        raise HTTPException(status_code=400, detail=email.failure_reason)
    return _email_payload(email, session)


@router.post("/send-bulk")
def send_bulk_candidate_emails(
    payload: SendCandidateEmailsRequest,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    sent = []
    failed = []
    for email_id in payload.email_ids:
        email = session.get(CandidateEmail, email_id)
        if not email:
            failed.append({"email_id": str(email_id), "reason": "Email draft not found."})
            continue
        if email.status != CandidateEmailStatus.draft:
            failed.append({"email_id": str(email_id), "reason": "Only draft emails can be sent."})
            continue
        try:
            send_candidate_email(email)
            sent.append(email)
        except CandidateEmailError as exc:
            email.status = CandidateEmailStatus.failed
            email.failure_reason = str(exc)
            failed.append({"email_id": str(email_id), "reason": str(exc)})
        except Exception as exc:
            email.status = CandidateEmailStatus.failed
            email.failure_reason = f"SMTP send failed: {exc}"
            failed.append({"email_id": str(email_id), "reason": email.failure_reason})
        email.updated_at = utc_now()
        session.add(email)
        session.commit()
    return {"sent": [_email_payload(email, session) for email in sent], "failed": failed}
