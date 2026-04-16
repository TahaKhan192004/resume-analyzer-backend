from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("resume_filter", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.task_routes = {"app.workers.tasks.*": {"queue": "analysis"}}
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.task_acks_late = True
celery_app.conf.broker_connection_retry_on_startup = True
