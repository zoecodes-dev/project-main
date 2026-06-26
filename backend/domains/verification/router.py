import uuid
from fastapi import APIRouter, Depends, status, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from backend.infrastructure.database import get_db
from backend.infrastructure.auth import CurrentUser, get_current_user

router = APIRouter(prefix="/verification", tags=["Verification"])