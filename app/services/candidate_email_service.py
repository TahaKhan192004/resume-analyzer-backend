import json
import imaplib
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.entities import (
    Applicant,
    CandidateEmail,
    CandidateEmailStatus,
    CandidateProfile,
    Decision,
    EvaluationDimensionResult,
    EvaluationRun,
    EvaluationStatus,
    FinalEvaluation,
    JobProfile,
)
from app.services.llm_client import DeepSeekClient, LLMError


class CandidateEmailError(RuntimeError):
    pass


class RejectionEmailDraft(BaseModel):
    subject: str = Field(min_length=5, max_length=140)
    body: str = Field(min_length=80, max_length=2500)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def latest_rejected_completed_analysis(session: Session, applicant_id, job_id) -> tuple[EvaluationRun, FinalEvaluation]:
    run = session.exec(
        select(EvaluationRun)
        .where(
            EvaluationRun.applicant_id == applicant_id,
            EvaluationRun.job_id == job_id,
            EvaluationRun.status == EvaluationStatus.completed,
        )
        .order_by(EvaluationRun.completed_at.desc())
    ).first()
    if not run:
        raise CandidateEmailError("No completed analysis exists for this applicant and job.")
    final = session.exec(
        select(FinalEvaluation)
        .where(FinalEvaluation.run_id == run.id, FinalEvaluation.decision == Decision.reject)
        .order_by(FinalEvaluation.created_at.desc())
    ).first()
    if not final:
        raise CandidateEmailError("The completed analysis is not a rejection decision.")
    return run, final


def _fallback_rejection_email(applicant: Applicant, job: JobProfile, final: FinalEvaluation) -> tuple[str, str]:
    name = (applicant.candidate_name or "there").strip()
    subject = f"Update on your application for {job.title}"
    gaps = [item.strip() for item in (final.gaps or []) if item and item.strip()]
    reason = gaps[0] if gaps else final.summary.strip()
    if reason:
        reason_sentence = (
            "At this stage, we are moving forward with candidates whose background more closely matches "
            f"the role requirements, especially around {reason[0].lower() + reason[1:] if reason else 'the required experience'}."
        )
    else:
        reason_sentence = "At this stage, we are moving forward with candidates whose background more closely matches the role requirements."
    body = f"""Hi {name},

Thank you for applying for the {job.title} position and for taking the time to share your profile with us.

After reviewing your application, we will not be moving forward with your candidacy for this role. {reason_sentence}

We appreciate your interest in the company and wish you the very best in your job search.

Best regards,
{get_settings().recruiter_from_name}
"""
    return subject, body


def _low_dimension_summary(dimensions: list[EvaluationDimensionResult]) -> list[dict[str, Any]]:
    low_dimensions = sorted(
        [dimension for dimension in dimensions if dimension.score is not None],
        key=lambda item: float(item.score or 0),
    )[:4]
    return [
        {
            "dimension": item.dimension,
            "score": item.score,
            "reasoning": (item.result_json or {}).get("reasoning", ""),
            "missing_information": (item.result_json or {}).get("missing_information", []),
            "red_flags": (item.result_json or {}).get("red_flags", []),
        }
        for item in low_dimensions
    ]


async def render_rejection_email(session: Session, applicant: Applicant, job: JobProfile, run: EvaluationRun, final: FinalEvaluation) -> tuple[str, str]:
    dimensions = session.exec(select(EvaluationDimensionResult).where(EvaluationDimensionResult.run_id == run.id)).all()
    profile = session.exec(select(CandidateProfile).where(CandidateProfile.applicant_id == applicant.id).order_by(CandidateProfile.created_at.desc())).first()
    settings = get_settings()
    evidence = {
        "candidate": {
            "name": applicant.candidate_name,
            "email": applicant.candidate_email,
            "applied_role": applicant.applied_role,
        },
        "job": {
            "title": job.title,
            "summary": job.summary,
            "requirements": job.requirements,
            "success_definition": job.success_definition,
        },
        "final_evaluation": {
            "score": final.final_score,
            "decision": final.decision.value,
            "summary": final.summary,
            "strengths": final.strengths,
            "gaps": final.gaps,
            "red_flags": final.red_flags,
            "missing_information": final.missing_information,
        },
        "lowest_scoring_dimensions": _low_dimension_summary(dimensions),
        "candidate_profile": (profile.profile_json if profile else {}),
    }
    system_prompt = (
        "You are an empathetic HR recruiter writing candidate rejection emails. "
        "Use the provided evaluation evidence only. Do not invent details. "
        "Be humble, warm, and useful. Avoid legal risk, harsh language, exact score disclosure, internal AI/process mentions, "
        "and overpromising future opportunities. Return JSON only."
    )
    user_prompt = f"""
Write a customized rejection email for this candidate.

Return JSON:
{{
  "subject": "short professional subject",
  "body": "plain text email body"
}}

Requirements:
- 140 to 220 words.
- Start with "Hi <candidate name>," or "Hi there," if name is missing.
- Thank them for applying for the exact job title.
- Clearly say we are not moving forward for this role.
- Include 2 to 3 constructive improvement areas from the evidence, phrased gently.
- If there are strengths, mention one positive note briefly.
- Make it engaging, professional, and human, not robotic.
- Do not mention AI, algorithms, scores, internal rubrics, red flags, or that this was auto-generated.
- Do not say they are unqualified; say the current match was not strong enough for this role.
- End with:
Best regards,
{settings.recruiter_from_name}

Evidence:
{json.dumps(evidence, ensure_ascii=False, default=str)}
"""
    try:
        result, _ = await DeepSeekClient().json_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=RejectionEmailDraft,
            temperature=0.4,
            max_tokens=1200,
        )
        return result.subject.strip(), result.body.strip()
    except (LLMError, Exception) as exc:
        raise CandidateEmailError(f"Could not generate AI rejection email: {exc}") from exc


def send_candidate_email(email: CandidateEmail) -> CandidateEmail:
    settings = get_settings()
    missing = []
    if not settings.smtp_host:
        missing.append("SMTP_HOST")
    if not settings.smtp_username:
        missing.append("SMTP_USERNAME")
    if not settings.smtp_password:
        missing.append("SMTP_PASSWORD")
    if missing:
        raise CandidateEmailError(f"Missing email settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = f"{settings.recruiter_from_name} <{email.from_email}>"
    message["To"] = email.to_email
    message.set_content(email.body)

    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_use_starttls:
                server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    sent_at = utc_now()
    save_warning = _save_sent_copy(message, sent_at) if settings.save_sent_email_copy else None
    email.status = CandidateEmailStatus.sent
    email.failure_reason = save_warning
    email.sent_at = sent_at
    email.updated_at = utc_now()
    return email


def _save_sent_copy(message: EmailMessage, sent_at: datetime) -> str | None:
    settings = get_settings()
    if not settings.imap_host or not settings.smtp_username or not settings.smtp_password:
        return "Sent via SMTP, but IMAP settings are missing so no Sent-folder copy was saved."
    if not message["Date"]:
        message["Date"] = format_datetime(sent_at)

    try:
        with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
            imap.login(settings.smtp_username, settings.smtp_password)
            mailbox = _resolve_sent_mailbox(imap, settings.sent_mailbox_name)
            _append_sent_message(imap, mailbox, sent_at, message.as_bytes())
            imap.logout()
    except Exception as exc:
        return f"Sent via SMTP, but could not save a copy to the Sent folder: {exc}"
    return None


def _append_sent_message(imap: imaplib.IMAP4_SSL, mailbox: str, sent_at: datetime, payload: bytes) -> None:
    attempts = [
        ("(\\Seen)", imaplib.Time2Internaldate(sent_at)),
        ("(\\Seen)", None),
        (None, None),
    ]
    last_error = None
    for flags, date_time in attempts:
        status, response = imap.append(mailbox, flags, date_time, payload)
        if status == "OK":
            return
        last_error = f"APPEND command error: {status} {response}"
    raise CandidateEmailError(last_error or "APPEND command failed")


def _resolve_sent_mailbox(imap: imaplib.IMAP4_SSL, preferred: str) -> str:
    status, folders = imap.list()
    candidates = [preferred, "Sent", "Sent Items", "INBOX.Sent", "[Gmail]/Sent Mail"]
    if status != "OK" or not folders:
        return preferred
    decoded = []
    for folder in folders:
        text = folder.decode("utf-8", errors="ignore")
        name = text.rsplit(' "/" ', 1)[-1].strip('"')
        decoded.append(name)
    for candidate in candidates:
        if candidate in decoded:
            return candidate
    for name in decoded:
        if "sent" in name.lower():
            return name
    return preferred
