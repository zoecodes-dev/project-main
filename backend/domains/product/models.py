# =============================================================================
# backend/domains/product/models.py
#
# KIRA Compliance Intelligence Platform — Product Domain ORM Models
#
# 구현 대상: schema.sql 영역 7 (Product / BOM / Parts)
#   - products, bom_versions, parts, bom_items,
#     part_code_mapping, manufacturing_process
#
# 설계 원칙 (PROJECT_CORE.md 5-1 ~ 5-5 준수):
#   1. 도메인 격리 — 타 도메인 모델 클래스 import 금지.
#      타 도메인 FK(suppliers)는 ForeignKey("테이블.컬럼") 문자열로만 선언,
#      relationship() 미선언. 조인은 Service 계층 쿼리에서 처리.
#   2. 상태 전이 금지 — bom_versions.status 직접 UPDATE 금지.
#      전이는 반드시 domains/product/state_machine.py 를 통해서만.
#   3. 자기참조 — parts.parent_part_id 기반 5계층 트리.
#      N차 전체 탐색은 재귀 CTE 사용 (이 파일은 단일 레벨 relationship만 제공).
#   4. Provenance — 모든 상태 변경은 audit_trail 자동 기록 대상.
#      (데코레이터 적용은 service/state_machine 레이어에서 수행)
#
# [DECISION_LOG 결정 #1 반영 — 2025-W2]
#   Product / BOM 은 "생성"이 아니라 "외부 원천에서 가져오기(import)".
#   - products, bom_versions 에 source_system / external_id / synced_at 컬럼 추가.
#   - created_at / updated_at 의미 = "이 시스템에 동기화된 시각" (원천 생성 시각 아님).
#   - 시연 환경에서는 source_system = 'SEED'.
#   - 실제 환경에서는 repository.fetch_from_source() 내부 데이터 소스만 교체하면 됨.
#   - ProductCreateRequest → ProductImportTrigger 로 의미 변경
#     (외부에서 필드를 받아 생성하는 폼이 아니라, 동기화를 트리거하는 요청).
#
# [W4 변경 — 2025-W4]
#   schema 변경에 따른 ORM 동기화.
#   - Customer 모델 신설 (__tablename__="customers").
#     고객사(BMW/Mercedes 등)를 ERP에서 Ingest하는 패턴. 도메인 내부 소유.
#   - Product 에 customer_id·model_name·amperage_ah 컬럼 추가.
#     같은 차종도 모델·암페어 조합이 다르면 BOM·공급망이 달라지므로 별도 row.
#   - BomVersion 의 effective_from/to → production_from/to 로 개명.
#     "규제 발효일"(regulations.effective_from)과 "생산 기간"을 명확히 분리.
#     regulations.effective_from 은 변경 없음.
# =============================================================================

from __future__ import annotations

import enum
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

from backend.infrastructure.database import Base


# ============================================================
# [1] SQLAlchemy ORM 모델 영역
# ============================================================

# ---------------------------------------------------------------------------
# BomVersionStatus Enum
# ---------------------------------------------------------------------------

class BomVersionStatus(str, enum.Enum):
    """
    BOM 버전 상태 열거형.

    불변 규칙:
      - 한 product에 active 버전은 반드시 1개.
      - deprecated → active 역전이 금지.
      - 상태 전이 로직은 domains/product/state_machine.py 에만 존재.
    """
    DRAFT      = "draft"
    ACTIVE     = "active"
    DEPRECATED = "deprecated"


# ---------------------------------------------------------------------------
# 0. Customer  ← W4 신설
# ---------------------------------------------------------------------------

class Customer(Base):
    """
    고객사(완성차 OEM) 마스터.

    [W4 신설 이유]
    같은 배터리 사양이라도 BMW iX3(108Ah)·i4(81Ah)처럼 고객사·모델·암페어 조합이
    다르면 투입 셀·BOM·협력사 계약이 달라져 DPP 증빙이 각각 필요해요.
    그 제품들이 공통으로 참조할 고객사 마스터가 이 테이블이에요.

    [결정 #1 동일 패턴]
    고객사도 원청 ERP가 원천. KIRA에서 직접 만들면 ERP 코드와 매핑이 깨질 수 있어서
    Ingest 패턴(source_system / external_id / synced_at)을 그대로 따라요.

    [도메인 소유]
    customers 테이블은 Product 도메인이 소유해요.
    products.customer_id 가 이 테이블을 참조하며, 도메인 내부 relationship으로 선언해요.
    """

    __tablename__ = "customers"

    customer_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="고객사 고유 식별자",
    )

    customer_code = Column(
        String(50),
        unique=True,
        nullable=False,
        comment="고객사 코드. UNIQUE. 예: 'BMW', 'MERCEDES'",
    )

    customer_name = Column(
        String(255),
        nullable=False,
        comment="고객사 표시명. 예: 'BMW AG', 'Mercedes-Benz AG'",
    )

    country = Column(
        String(2),
        nullable=True,
        comment="고객사 소재 국가 코드 (ISO 3166-1 alpha-2). 예: 'DE'",
    )

    # ------------------------------------------------------------------
    # [결정 #1] 외부 원천 추적 컬럼 3종
    # ------------------------------------------------------------------
    source_system = Column(
        String(100),
        nullable=True,
        server_default="ERP_PLM",
        comment=(
            "[결정 #1] 데이터 출처 식별자. "
            "허용값: 'ERP_PLM'(기본) / 'SEED'(시연). "
            "fetch_from_source() 에서 반드시 세팅."
        ),
    )

    external_id = Column(
        String(255),
        nullable=True,
        comment=(
            "[결정 #1] 원천 시스템의 원본 고객사 PK(문자열). "
            "UPSERT 시 ON CONFLICT(customer_code) 기준점."
        ),
    )

    synced_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "[결정 #1] 이 시스템에 마지막으로 동기화된 UTC 시각. "
            "fetch_from_source() 호출 시 datetime.now(timezone.utc) 로 갱신."
        ),
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="[결정 #1] 이 시스템에 처음 동기화된 시각. 원천 생성 시각 아님.",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships
    # ------------------------------------------------------------------
    products: Mapped[List["Product"]] = relationship(
        "Product",
        back_populates="customer",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Customer code={self.customer_code!r} "
            f"name={self.customer_name!r} "
            f"country={self.country!r}>"
        )


# ---------------------------------------------------------------------------
# 1. Product
# ---------------------------------------------------------------------------

class Product(Base):
    """
    배터리 제품 마스터.

    모든 공급망·DPP 흐름의 최상위 기준점.
    product_code UNIQUE — 중복 등록 방지.

    [결정 #1] 이 시스템은 제품을 직접 "생성"하지 않는다.
    원청 ERP/MES/PLM 이 원천. 이 시스템은 동기화된 복사본을 보유(read-mostly).
      - source_system: 데이터 출처 식별. 시연='SEED', 실환경='ERP'|'MES'|'PLM'
      - external_id:   원천 시스템의 PK. 원천 row와 1:1 추적에 사용.
      - synced_at:     이 시스템에 마지막으로 동기화된 UTC 시각.
                       created_at/updated_at 과 달리 "원천 데이터 최신성" 기준.

    [도메인 격리]
    manufacturer_id → suppliers.supplier_id
      ForeignKey 문자열 참조만 선언. relationship 없음.
      제조사 정보가 필요한 경우 Service 계층에서 JOIN 쿼리로 처리.
    """

    __tablename__ = "products"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    product_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="제품 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 식별·명칭
    # ------------------------------------------------------------------
    product_code = Column(
        String(50),
        unique=True,
        nullable=False,
        comment="제품 코드. UNIQUE. 예: 'BAT-NCM811-100Ah'",
    )

    product_name = Column(
        String(255),
        nullable=True,
        comment="제품 표시명",
    )

    # ------------------------------------------------------------------
    # 테넌트(소유 원청) — 테넌트 격리(§0.2). 마이그레이션 0002 에서 추가.
    #   baseline 에는 없던 컬럼. nullable(suppliers/batches.tenant_id 와 동일 정책).
    # ------------------------------------------------------------------
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id"),
        nullable=True,
        comment="소유 원청 테넌트 FK → tenants.tenant_id. 목록/상세 테넌트 격리 기준.",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 FK (Customer) — relationship 선언
    # ------------------------------------------------------------------
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.customer_id"),
        nullable=True,
        comment=(
            "고객사 FK → customers.customer_id. "
            "같은 고객사의 여러 모델이 이 FK를 공유해요."
        ),
    )

    # ------------------------------------------------------------------
    # 타 도메인 FK (Supplier Domain) — relationship 미선언
    # ------------------------------------------------------------------
    manufacturer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=True,
        comment=(
            "제조사 협력사 FK → suppliers.supplier_id. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    # ------------------------------------------------------------------
    # 제품 속성
    # ------------------------------------------------------------------
    type = Column(
        String(50),
        nullable=True,
        comment="배터리 형태. 예: 각형 / 파우치형 / 원통형",
    )

    model_name = Column(
        String(100),
        nullable=True,
        comment=(
            "차량 모델명. 예: 'iX3', 'i4', 'GLC'. "
            "같은 고객사라도 모델이 다르면 BOM·협력사가 달라 별도 product row로 관리해요."
        ),
    )

    amperage_ah = Column(
        Numeric(10, 2),
        nullable=True,
        comment=(
            "배터리 용량(Ah). 예: 108.00, 81.00. "
            "고객사+모델+암페어 조합이 달라지면 DPP 증빙 대상이 달라지므로 별도 식별 키 역할."
        ),
    )

    specs = Column(
        JSONB,
        nullable=True,
        comment=(
            "제품 규격(JSONB). "
            "예: {\"무게\": \"650kg\", \"용량\": \"100Ah\", \"전압\": \"3.7V\"}"
        ),
    )

    # ------------------------------------------------------------------
    # [결정 #1] 외부 원천 추적 컬럼 3종
    # schema.sql 일괄수정 대상 — 컬럼 추가 후 migration 필요
    # ------------------------------------------------------------------
    source_system = Column(
        String(100),
        nullable=True,
        comment=(
            "[결정 #1] 데이터 출처 식별자. "
            "허용값: 'SEED'(시연) / 'ERP' / 'MES' / 'PLM'. "
            "NULL이면 출처 미확인 — repository.fetch_from_source()에서 반드시 세팅."
        ),
    )

    external_id = Column(
        String(255),
        nullable=True,
        comment=(
            "[결정 #1] 원천 시스템의 원본 PK(문자열 변환). "
            "원천 row와 1:1 추적용. UPSERT 시 ON CONFLICT(product_code) 사용."
        ),
    )

    synced_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "[결정 #1] 이 시스템에 마지막으로 동기화된 UTC 시각. "
            "fetch_from_source() 호출 시 datetime.now(timezone.utc) 로 갱신. "
            "datetime.utcnow() 사용 금지(deprecated)."
        ),
    )

    # ------------------------------------------------------------------
    # 타임스탬프
    # [결정 #1] 의미 재정의:
    #   created_at = 이 시스템에 처음 동기화된 시각 (원천 생성 시각 아님)
    #   updated_at = 이 시스템에서 마지막으로 갱신된 시각
    # ------------------------------------------------------------------
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="[결정 #1] 이 시스템에 처음 동기화된 시각. 원천 생성 시각 아님.",
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="[결정 #1] 이 시스템에서 마지막으로 갱신된 시각.",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships
    # ------------------------------------------------------------------
    customer: Mapped[Optional["Customer"]] = relationship(
        "Customer",
        back_populates="products",
        lazy="select",
    )

    bom_versions: Mapped[List["BomVersion"]] = relationship(
        "BomVersion",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Product code={self.product_code!r} "
            f"source={self.source_system!r} "
            f"synced_at={self.synced_at!r}>"
        )


# ---------------------------------------------------------------------------
# 2. BomVersion
# ---------------------------------------------------------------------------

class BomVersion(Base):
    """
    제품 BOM 버전 관리.

    같은 제품도 시점별로 다른 BOM을 가질 수 있음.

    [결정 #1] BOM도 외부 원천(ERP/PLM)에서 가져오는 복사본.
      - source_system / external_id / synced_at 컬럼 동일하게 추가.
      - BOMImported 이벤트 발행은 service.py 에서 담당.

    [불변 규칙 — PROJECT_CORE.md 3-1]
    status ∈ {draft, active, deprecated}
      - 한 product에 active 버전은 1개만 허용.
      - 상태 전이는 반드시 domains/product/state_machine.py 경유.
      - 이 클래스에서 status를 직접 갱신하는 메서드 작성 금지.

    [도메인 격리]
    approved_by → users.user_id
      ForeignKey 문자열 참조만 선언. relationship 없음.
    """

    __tablename__ = "bom_versions"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    bom_version_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="BOM 버전 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 FK
    # ------------------------------------------------------------------
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.product_id", ondelete="CASCADE"),
        nullable=False,
        comment="소속 제품 FK",
    )

    # ------------------------------------------------------------------
    # 버전 정보
    # ------------------------------------------------------------------
    version_number = Column(
        String(20),
        nullable=False,
        comment="버전 식별 번호. 예: 'v1.0', 'v2.3'",
    )

    production_from = Column(
        Date,
        nullable=True,
        comment=(
            "이 BOM 버전으로 실제 생산을 시작한 날짜. "
            "구 effective_from(규제 발효일) 과 혼동 주의 — "
            "regulations.effective_from 은 그대로이고, 여기는 '생산 기간' 기준."
        ),
    )

    production_to = Column(
        Date,
        nullable=True,
        comment=(
            "이 BOM 버전 생산 종료일. NULL이면 현재도 생산 중. "
            "as_of 날짜 조회 시: production_from <= as_of <= COALESCE(production_to, now())."
        ),
    )

    # ------------------------------------------------------------------
    # 상태
    # [핵심] 직접 UPDATE 금지 — state_machine.py 경유 필수
    # ------------------------------------------------------------------
    status = Column(
        String(20),
        nullable=False,
        default=BomVersionStatus.DRAFT.value,
        server_default=BomVersionStatus.DRAFT.value,
        comment=(
            "BOM 버전 상태. 허용값: draft / active / deprecated. "
            "한 product에 active 버전은 1개만 존재. "
            "직접 UPDATE 금지 — domains/product/state_machine.py 경유."
        ),
    )

    # ------------------------------------------------------------------
    # 승인 정보 — 타 도메인 FK, relationship 미선언
    # ------------------------------------------------------------------
    approved_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=True,
        comment=(
            "승인자 FK → users.user_id. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    approved_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="승인 처리 일시",
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="[결정 #1] 이 시스템에 처음 동기화된 시각. 원천 생성 시각 아님.",
    )

    # ------------------------------------------------------------------
    # [결정 #1] 외부 원천 추적 컬럼 3종 (products와 동일 패턴)
    # ------------------------------------------------------------------
    source_system = Column(
        String(100),
        nullable=True,
        comment=(
            "[결정 #1] 데이터 출처 식별자. "
            "허용값: 'SEED'(시연) / 'ERP' / 'PLM'. "
            "fetch_from_source() 에서 반드시 세팅."
        ),
    )

    external_id = Column(
        String(255),
        nullable=True,
        comment=(
            "[결정 #1] 원천 시스템의 원본 BOM PK(문자열). "
            "원천 BOM row와 1:1 추적용."
        ),
    )

    synced_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "[결정 #1] 이 시스템에 마지막으로 동기화된 UTC 시각. "
            "fetch_from_source() 호출 시 datetime.now(timezone.utc) 로 갱신."
        ),
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships
    # ------------------------------------------------------------------
    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="bom_versions",
    )

    bom_items: Mapped[List["BomItem"]] = relationship(
        "BomItem",
        back_populates="bom_version",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<BomVersion product_id={self.product_id!r} "
            f"version={self.version_number!r} "
            f"status={self.status!r} "
            f"source={self.source_system!r}>"
        )


# ---------------------------------------------------------------------------
# 3. Part
# ---------------------------------------------------------------------------

class Part(Base):
    """
    부품 마스터 — Pack→Module→Cell→전구체→광물 7계층 0-base 자기참조 트리.

    [자기참조 설계]
    parent_part_id = ForeignKey("parts.part_id")
      - NULL이면 루트 노드(Pack, tier_level=1).
      - children relationship: 이 부품의 직접 하위 부품 목록.
      - parent  relationship: 이 부품의 직접 상위 부품.
      - remote_side=[part_id] 명시 — SQLAlchemy가 FK 방향을 자동 추론
        불가능하므로 "많은 쪽(자식) → 하나(부모)" 방향을 명확히 지정.

    [N차 전체 트리 탐색]
    relationship.children 재귀 순회 금지.
    반드시 재귀 CTE (WITH RECURSIVE)로 처리 — PROJECT_CORE.md 5-5.

    [FTA 필수 조건]
    hs_code 6자리 이상 필수. Service 레이어에서 6자리 미만 입력 시 422 반환.

    [결정 #1 반영 — schema.sql 업데이트로 parts도 대상에 포함됨]
    schema.sql L436~438 "결정 #1 누락 정형화" 주석과 함께 3컬럼 추가됨.
    source_system / external_id / synced_at — products/bom_versions/bom_items와 동일 패턴.
    """

    __tablename__ = "parts"

    part_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="부품 고유 식별자",
    )

    part_code = Column(
        String(50),
        unique=True,
        nullable=False,
        comment="원청 기준 부품 코드. UNIQUE. 예: 'PACK-NCM811-100Ah'",
    )

    part_name = Column(
        String(255),
        nullable=True,
        comment="부품 표시명",
    )

    tier_level = Column(
        Integer,
        nullable=True,
        comment="부품 계층 레벨. 0=Pack / 1=Module / 2=Cell / 3=활물질·CAM / 4=전구체 / 5=제련·정제 / 6=광산",
    )

    parent_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id"),
        nullable=True,
        comment=(
            "상위 부품 자기참조 FK. "
            "NULL이면 루트(Pack). "
            "N차 탐색은 재귀 CTE 사용 — relationship 재귀 순회 금지."
        ),
    )

    hs_code = Column(
        String(15),
        nullable=True,
        comment=(
            "HS Code (6자리 이상 필수). "
            "FTA 세번변경기준(CTC2/CTC4/CTC6) 판정 키. "
            "6자리 미만 → API 422."
        ),
    )

    material_type = Column(
        String(100),
        nullable=True,
        comment="소재 유형. 예: NCM 811, LFP, 코발트",
    )

    function_purpose = Column(
        Text,
        nullable=True,
        comment="부품 기능 설명",
    )

    unit_price = Column(
        Numeric(15, 4),
        nullable=True,
        comment=(
            "단가. RVC(Regional Value Content) 부가가치기준 FTA 판정 계산에 사용. "
            "NULL이면 RVC 계산 불가 → Service 레이어에서 경고."
        ),
    )

    purchase_unit = Column(
        String(20),
        nullable=True,
        comment="구매 단위. 예: kg / 개 / MWh",
    )

    specs = Column(
        JSONB,
        nullable=True,
        comment="부품 규격(JSONB).",
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # ------------------------------------------------------------------
    # [결정 #1] 외부 원천 추적 컬럼 3종
    # schema.sql L436~438 "결정 #1 누락 정형화" 반영
    # ------------------------------------------------------------------
    source_system = Column(
        String(100),
        nullable=True,
        server_default="ERP_PLM",
        comment=(
            "[결정 #1] 데이터 출처 식별자. "
            "허용값: 'ERP_PLM'(기본) / 'SEED'(시연). "
            "schema DEFAULT 'ERP_PLM' 과 일치."
        ),
    )

    external_id = Column(
        String(255),
        nullable=True,
        comment=(
            "[결정 #1] 원천 시스템의 원본 Part PK(문자열). "
            "원천 row와 1:1 추적용."
        ),
    )

    synced_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "[결정 #1] 이 시스템에 마지막으로 동기화된 UTC 시각. "
            "fetch_from_source() 호출 시 datetime.now(timezone.utc) 로 갱신."
        ),
    )

    # 자기참조 Relationships
    children: Mapped[List["Part"]] = relationship(
        "Part",
        back_populates="parent",
        foreign_keys=[parent_part_id],
        lazy="select",
        cascade="all",
    )

    parent: Mapped[Optional["Part"]] = relationship(
        "Part",
        back_populates="children",
        foreign_keys=[parent_part_id],
        remote_side="Part.part_id",
        lazy="select",
    )

    bom_items: Mapped[List["BomItem"]] = relationship(
        "BomItem",
        back_populates="part",
        lazy="select",
    )

    part_code_mappings: Mapped[List["PartCodeMapping"]] = relationship(
        "PartCodeMapping",
        back_populates="part",
        cascade="all, delete-orphan",
        lazy="select",
    )

    manufacturing_processes: Mapped[List["ManufacturingProcess"]] = relationship(
        "ManufacturingProcess",
        back_populates="part",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Part code={self.part_code!r} "
            f"tier_level={self.tier_level!r} "
            f"parent_part_id={self.parent_part_id!r}>"
        )


# ---------------------------------------------------------------------------
# 4. BomItem
# ---------------------------------------------------------------------------

class BomItem(Base):
    """
    BOM 버전 내 부품 구성 항목.
    부품별 소요량·원산지·직접재료비 기록.
    """

    __tablename__ = "bom_items"

    bom_item_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="BOM 항목 고유 식별자",
    )

    bom_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("bom_versions.bom_version_id", ondelete="CASCADE"),
        nullable=False,
        comment="소속 BOM 버전 FK",
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id"),
        nullable=True,
        comment="해당 부품 FK",
    )

    required_quantity = Column(
        Numeric(15, 4),
        nullable=True,
        comment="소요 수량",
    )

    required_quantity_unit = Column(
        String(20),
        nullable=True,
        comment="소요 수량 단위. 예: kg / 개 / L",
    )

    percentage = Column(
        Numeric(5, 2),
        nullable=True,
        comment="전체 BOM 대비 이 부품의 구성 비율(%)",
    )

    direct_material_cost = Column(
        Numeric(15, 4),
        nullable=True,
        comment="직접재료비. RVC 계산 시 역내 부가가치 산정 기준.",
    )

    origin_country = Column(
        String(2),
        nullable=True,
        comment="원산지 국가 코드 (ISO 3166-1 alpha-2). FTA 원산지 기준 판정 입력값.",
    )

    # ------------------------------------------------------------------
    # [결정 #1] 외부 원천 추적 컬럼 3종
    # schema.sql L454~457 "결정 #1 누락 정형화" 반영
    # ------------------------------------------------------------------
    source_system = Column(
        String(100),
        nullable=True,
        server_default="ERP_PLM",
        comment=(
            "[결정 #1] 데이터 출처 식별자. "
            "schema DEFAULT 'ERP_PLM' 과 일치."
        ),
    )

    external_id = Column(
        String(255),
        nullable=True,
        comment="[결정 #1] 원천 시스템의 원본 BomItem PK(문자열).",
    )

    synced_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "[결정 #1] 이 시스템에 마지막으로 동기화된 UTC 시각."
        ),
    )    
    
    bom_version: Mapped["BomVersion"] = relationship(
        "BomVersion",
        back_populates="bom_items",
    )

    part: Mapped[Optional["Part"]] = relationship(
        "Part",
        back_populates="bom_items",
    )

    def __repr__(self) -> str:
        return (
            f"<BomItem bom_version_id={self.bom_version_id!r} "
            f"part_id={self.part_id!r} qty={self.required_quantity!r}>"
        )


# ---------------------------------------------------------------------------
# 5. PartCodeMapping
# ---------------------------------------------------------------------------

class PartCodeMapping(Base):
    """
    원청 코드 ↔ 협력사 코드 매핑 브릿지 테이블.

    [도메인 격리]
    supplier_id → suppliers.supplier_id
      ForeignKey 문자열 참조만 선언. relationship 없음.
    """

    __tablename__ = "part_code_mapping"

    mapping_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="매핑 고유 식별자",
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id", ondelete="CASCADE"),
        nullable=False,
        comment="원청 기준 부품 FK",
    )

    supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=True,
        comment=(
            "협력사 FK → suppliers.supplier_id. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    supplier_part_code = Column(
        String(50),
        nullable=True,
        comment="협력사 내부 사용 부품 코드. 예: 'POS-CAM-NCM-811-A'",
    )

    original_part_code = Column(
        String(50),
        nullable=True,
        comment="원청 기준 부품 코드. 예: 'CAM-NCM811'",
    )

    part: Mapped["Part"] = relationship(
        "Part",
        back_populates="part_code_mappings",
    )

    def __repr__(self) -> str:
        return (
            f"<PartCodeMapping part_id={self.part_id!r} "
            f"supplier_part_code={self.supplier_part_code!r}>"
        )


# ---------------------------------------------------------------------------
# 6. ManufacturingProcess
# ---------------------------------------------------------------------------

class ManufacturingProcess(Base):
    """
    부품별 제조 공정도.

    [도메인 격리]
    outsourced_to_supplier_id → suppliers.supplier_id
      ForeignKey 문자열 참조만 선언. relationship 없음.

    [유효성 검사]
    is_outsourced=True + outsourced_to_supplier_id=NULL → Service 레이어에서 HTTP 422.
    """

    __tablename__ = "manufacturing_process"

    process_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="공정 고유 식별자",
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id", ondelete="CASCADE"),
        nullable=False,
        comment="해당 부품 FK",
    )

    sequence_no = Column(
        Integer,
        nullable=True,
        comment="공정 순서 번호. 작을수록 앞 공정.",
    )

    process_name = Column(
        String(255),
        nullable=True,
        comment="공정명. 예: '양극재 소성', '조립', '화성'",
    )

    process_description = Column(
        Text,
        nullable=True,
        comment="공정 상세 설명",
    )

    is_outsourced = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="아웃소싱 공정 여부. True이면 outsourced_to_supplier_id 필수.",
    )

    outsourced_to_supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=True,
        comment=(
            "아웃소싱 대상 협력사 FK → suppliers.supplier_id. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    process_image_url = Column(
        String(500),
        nullable=True,
        comment="제조 공정도 이미지 URL.",
    )

    part: Mapped["Part"] = relationship(
        "Part",
        back_populates="manufacturing_processes",
    )

    def __repr__(self) -> str:
        return (
            f"<ManufacturingProcess part_id={self.part_id!r} "
            f"seq={self.sequence_no!r} "
            f"outsourced={self.is_outsourced!r}>"
        )


# ============================================================
# [2] Pydantic 입출력 스키마(DTO) 영역
# ============================================================

class ProductImportTrigger(BaseModel):
    """
    [결정 #1] 제품 동기화 트리거 요청 바디.

    이전 이름: ProductCreateRequest
    변경 이유: 이 시스템은 제품을 직접 생성하지 않는다.
               외부 원천(ERP/MES)에서 동기화하는 트리거이며,
               시연 환경에서는 시드 데이터가 원천.

    source_system: 동기화할 원천 시스템 지정.
                   미입력 시 'SEED' 사용(시연 기본값).
    """
    source_system: str = "SEED"


class ProductBrief(BaseModel):
    """
    목록·단건 응답용 직렬화 스키마.

    [결정 #1] source_system, synced_at 추가.
    [W4] customer_id, model_name, amperage_ah 추가.
    프론트엔드 제품 목록에서 고객사·모델·암페어 필터링에 사용돼요.
    """
    product_id: uuid.UUID
    product_code: str
    product_name: Optional[str] = None
    type: Optional[str] = None
    manufacturer_id: Optional[uuid.UUID] = None
    customer_id: Optional[uuid.UUID] = None      # W4 추가
    model_name: Optional[str] = None             # W4 추가
    amperage_ah: Optional[float] = None          # W4 추가
    source_system: Optional[str] = None          # 결정 #1
    synced_at: Optional[str] = None              # 결정 #1 (ISO 8601 문자열)

    model_config = {"from_attributes": True}


class BomTreeResponse(BaseModel):
    """
    BOM 트리 응답 스키마.

    active BOM 버전이 없으면 service에서 404 반환.
    only_confirmed=True(기본값)이면 link_status='confirmed' 노드만 포함.
    only_confirmed=False이면 pending 포함 전체 트리.
    """
    product_id: uuid.UUID
    product_code: str
    bom_version: str
    bom_status: str
    only_confirmed: bool   # 결정 #2 반영: 응답에 필터 조건 명시
    tree: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": False}


# ============================================================
# 편의 상수 — Service/State Machine 레이어에서 import하여 사용
# ============================================================

# BOM 버전 상태 허용값 집합 (state_machine.py 전이 검증용)
VALID_BOM_STATUSES: frozenset[str] = frozenset(
    s.value for s in BomVersionStatus
)

# BOM 버전 상태 전이 허용 매트릭스
# { from_status: {to_status, ...} }
BOM_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    BomVersionStatus.DRAFT.value: frozenset({
        BomVersionStatus.ACTIVE.value,
        BomVersionStatus.DEPRECATED.value,
    }),
    BomVersionStatus.ACTIVE.value: frozenset({
        BomVersionStatus.DEPRECATED.value,
        # active → draft 역전이 금지 (PROJECT_CORE.md 3-2)
    }),
    BomVersionStatus.DEPRECATED.value: frozenset(),  # 터미널 상태
}

# [결정 #1] 유효한 source_system 허용값
VALID_SOURCE_SYSTEMS: frozenset[str] = frozenset({
    "SEED",   # 시연 환경 시드 데이터
    "ERP",    # 원청 ERP 시스템
    "MES",    # 원청 MES 시스템
    "PLM",    # 원청 PLM 시스템
})