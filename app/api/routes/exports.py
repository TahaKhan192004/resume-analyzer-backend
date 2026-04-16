from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.entities import User
from app.services.csv_service import build_export_csv

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/csv")
def export_csv(
    job_id: UUID,
    decision: str | None = None,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_user),
):
    content = build_export_csv(session, job_id=job_id, decision=decision)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="enriched_applicants.csv"'},
    )

