from fastapi import APIRouter

from app.api.routes import applicants, auth, candidate_emails, exports, imports, jobs, prompts

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(jobs.router)
api_router.include_router(prompts.router)
api_router.include_router(imports.router)
api_router.include_router(applicants.router)
api_router.include_router(candidate_emails.router)
api_router.include_router(exports.router)
