from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.entities import PromptTemplate, PromptTemplateVersion, User
from app.schemas.contracts import PromptTemplatePayload

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("")
def list_prompts(session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    templates = session.exec(select(PromptTemplate).order_by(PromptTemplate.key)).all()
    return templates


@router.post("")
def create_prompt(payload: PromptTemplatePayload, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    template = PromptTemplate(key=payload.key, name=payload.name, description=payload.description)
    session.add(template)
    session.flush()
    version = PromptTemplateVersion(
        template_id=template.id,
        version=1,
        system_prompt=payload.system_prompt,
        task_prompt=payload.task_prompt,
        rubric_instructions=payload.rubric_instructions,
        output_schema=payload.output_schema,
        evaluation_hints=payload.evaluation_hints,
        role_notes=payload.role_notes,
        model_name=payload.model_name,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    template.active_version_id = version.id
    session.add(template)
    session.commit()
    return {**template.model_dump(), "active_version": version.model_dump()}


@router.get("/{template_id}")
def get_prompt(template_id: UUID, session: Session = Depends(get_session), _: User = Depends(get_current_user)):
    template = session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Prompt not found")
    versions = session.exec(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id).order_by(PromptTemplateVersion.version.desc())
    ).all()
    return {**template.model_dump(), "versions": [version.model_dump() for version in versions]}


@router.post("/{template_id}/versions")
def create_prompt_version(
    template_id: UUID,
    payload: PromptTemplatePayload,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    template = session.get(PromptTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Prompt not found")
    latest = session.exec(
        select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id).order_by(PromptTemplateVersion.version.desc())
    ).first()
    version = PromptTemplateVersion(
        template_id=template.id,
        version=(latest.version + 1 if latest else 1),
        system_prompt=payload.system_prompt,
        task_prompt=payload.task_prompt,
        rubric_instructions=payload.rubric_instructions,
        output_schema=payload.output_schema,
        evaluation_hints=payload.evaluation_hints,
        role_notes=payload.role_notes,
        model_name=payload.model_name,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    template.name = payload.name
    template.description = payload.description
    template.active_version_id = version.id
    session.add(template)
    session.commit()
    return version

