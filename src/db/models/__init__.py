"""ORM models package.

Importing this package as a side effect registers every model with
``Base.metadata``. Alembic env.py imports it so autogenerate sees the full
schema; tests import individual model classes directly.
"""

from __future__ import annotations

from src.db.models.application import Application
from src.db.models.application_score import ApplicationScore
from src.db.models.audit_log import AuditLog
from src.db.models.candidate import Candidate
from src.db.models.cv_document import CvDocument
from src.db.models.interview_booking import InterviewBooking
from src.db.models.job import Job
from src.db.models.public_upload_token import PublicUploadToken
from src.db.models.session import AuthSession
from src.db.models.talent_pool_entry import TalentPoolEntry
from src.db.models.user import User
from src.db.models.whatsapp import WhatsAppConversation, WhatsAppMessage

__all__ = [
    "Application",
    "ApplicationScore",
    "AuditLog",
    "AuthSession",
    "Candidate",
    "CvDocument",
    "InterviewBooking",
    "Job",
    "PublicUploadToken",
    "TalentPoolEntry",
    "User",
    "WhatsAppConversation",
    "WhatsAppMessage",
]
