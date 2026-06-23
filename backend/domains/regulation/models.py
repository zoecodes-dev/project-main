"""
backend/domains/regulation/models.py  (담당: 팀원 C — 은지)

★ [B1] regulation 도메인 ORM 모델 + Pydantic 응답 DTO

[이 파일의 역할]
  schema.sql 영역 10(규제/컴플라이언스)의 `regulations` 테이블을
  SQLAlchemy ORM 모델로 정의한다.
  router.py → service.py → repository.py → 여기(models.py) 단방향 의존.

[컬럼 매핑 — schema.sql과 1:1 대응]
  regulations 테이블:
    regulation_id    UUID PK          → Regulation.regulation_id
    name             VARCHAR(100)     → Regulation.name
    regulation_code  VARCHAR(50) UQ   → Regulation.regulation_code
    region           VARCHAR(10)      → Regulation.region  (EU / US / BOTH)
    description      TEXT             → Regulation.description
    version          VARCHAR(20)      → Regulation.version
    effective_from   DATE             → Regulation.effective_from
    document_s3_url  VARCHAR(500)     → Regulation.document_s3_url
    embedding_status VARCHAR(20)      → Regulation.embedding_status (pending / indexed)
    embedding        vector(1536)     → ❌ ORM 매핑 제외 (pgvector는 raw SQL로 처리)

  [embedding 컬럼을 ORM에서 제외하는 이유]
    pgvector의 vector(1536)는 SQLAlchemy 기본 타입에 없어서
    ORM에 매핑하면 별도 extension이 필요하고 SELECT * 시 매번 1536차원
    벡터가 메모리에 올라온다. compliance.py와 동일하게 raw SQL(text())로
    임베딩 연산만 따로 처리하는 게 효율적이다.

[TODO — D(영수) 선행 머지 후 활성화]
  regulation_required_fields 테이블: D가 DDL을 작성 중(C1 작업).
  DDL 머지 후 하단의 RegulationRequiredField ORM 주석을 해제한다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Date, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.database import Base


# ============================================================
# 1. regulations 테이블 ORM
# ============================================================

class Regulation(Base):
    """
    적용 규제 마스터 (schema.sql 영역 10).

    EU 배터리법, IRA FEOC, UFLPA 등 10대 글로벌 규제를 관리한다.
    regulation_code가 UNIQUE 키로, compliance.py의
    REGULATION_BY_DESTINATION 딕셔너리 값과 1:1 대응한다.
    """
    __tablename__ = "regulations"

    # ── PK ──
    regulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── 기본 속성 ──
    name: Mapped[Optional[str]] = mapped_column(String(100))
    regulation_code: Mapped[Optional[str]] = mapped_column(
        String(50),
        unique=True,    # schema.sql: UNIQUE
    )
    region: Mapped[Optional[str]] = mapped_column(
        String(10),     # CHECK (region IN ('EU', 'US', 'BOTH'))
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[Optional[str]] = mapped_column(String(20))
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    document_s3_url: Mapped[Optional[str]] = mapped_column(String(500))

    # ── 임베딩 관련 ──
    # embedding_status 만 ORM에 매핑. embedding(vector) 컬럼은 raw SQL로 처리.
    embedding_status: Mapped[Optional[str]] = mapped_column(
        String(20),
        default="pending",   # CHECK (embedding_status IN ('pending', 'indexed'))
    )

    # ── embedding vector(1536) 컬럼은 ORM에 매핑하지 않음 ──
    # 이유: pgvector 타입을 ORM에 매핑하면 SELECT * 시 매번 1536차원 벡터가
    #       메모리에 로드되어 비효율적. raw SQL로 필요할 때만 접근한다.
    #       (compliance.py search_regulations, seed_regulation_embeddings.py 참조)

    # ── TODO: D 머지 후 regulation_required_fields relationship 추가 ──
    # required_fields = relationship("RegulationRequiredField", back_populates="regulation")


# ============================================================
# 2. [TODO] regulation_required_fields 테이블 ORM
#    D(영수)의 C1 작업(DDL 배포) 완료 후 주석 해제
# ============================================================

# ┌──────────────────────────────────────────────────────────────┐
# │ D(영수)가 C1에서 아래 DDL을 머지한 후 이 블록 전체를 해제.   │
# │                                                              │
# │ 주석 해제 체크리스트:                                          │
# │   1. schema.sql에 regulation_required_fields 테이블 존재 확인  │
# │   2. docker compose down -v && up --build 로 DDL 반영          │
# │   3. 아래 class 주석 해제                                      │
# │   4. 위 Regulation ORM의 required_fields relationship 해제      │
# │   5. repository.py의 get_required_fields() TODO 구현 교체       │
# └──────────────────────────────────────────────────────────────┘

# class RegulationRequiredField(Base):
#     """
#     규제별 필수 제출 필드 매트릭스 (D가 DDL 생성 담당).
#
#     [테이블 구조 — D와 합의된 DDL]
#       regulation_id             UUID FK → regulations
#       field_name                VARCHAR    — 필드 식별자 (snake_case)
#       field_type                VARCHAR    — 데이터 타입 (number/string/jsonb 등)
#       provider_type_applicable  JSONB      — 적용 공급사 유형 배열
#     """
#     __tablename__ = "regulation_required_fields"
#
#     field_id: Mapped[uuid.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
#     )
#     regulation_id: Mapped[uuid.UUID] = mapped_column(
#         UUID(as_uuid=True),
#         ForeignKey("regulations.regulation_id"),
#         nullable=False,
#     )
#     field_name: Mapped[str] = mapped_column(String(100), nullable=False)
#     field_type: Mapped[Optional[str]] = mapped_column(String(50))
#     provider_type_applicable: Mapped[Optional[dict]] = mapped_column(JSONB)
#
#     regulation = relationship("Regulation", back_populates="required_fields")


# ============================================================
# 3. Pydantic 응답 DTO (router.py가 반환하는 응답 스키마)
# ============================================================

class RegulationResponse(BaseModel):
    """
    GET /regulations 응답용 DTO.

    ORM Regulation 객체를 이 스키마로 변환하여 반환한다.
    from_attributes=True 로 설정하면 ORM 객체를 그대로
    넣어도 Pydantic이 자동으로 필드를 매핑해 준다.

    사용 예시:
      regulation_orm = await repo.get_by_code(db, "EU_BATTERY")
      response = RegulationResponse.model_validate(regulation_orm)
    """
    regulation_id: uuid.UUID
    regulation_code: Optional[str] = None
    name: Optional[str] = None
    region: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    effective_from: Optional[date] = None
    embedding_status: Optional[str] = None

    model_config = {"from_attributes": True}


class RequiredFieldResponse(BaseModel):
    """
    GET /regulations/{code}/required-fields 응답용 DTO.

    [현재 상태] D의 DDL 머지 전까지 더미 데이터를 반환.
    [머지 후] RegulationRequiredField ORM 에서 변환.
    """
    field_name: str
    field_type: Optional[str] = None
    is_mandatory: bool = True
    provider_type_applicable: Optional[list[str]] = None

    model_config = {"from_attributes": True}
