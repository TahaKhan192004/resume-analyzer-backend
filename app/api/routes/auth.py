from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models.entities import User
from app.schemas.contracts import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(user.email))

