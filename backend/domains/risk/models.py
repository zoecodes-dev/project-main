from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, Numeric, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

from backend.infrastructure.database import Base

from backend.domains.supplier.models import SupplierRiskProfile as RiskProfile  # supplier 도메인이 SSOT