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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship  # Mapped 추가
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
# 1. Product
# ---------------------------------------------------------------------------

class Product(Base):
    """
    배터리 제품 마스터.

    모든 공급망·DPP 흐름의 최상위 기준점.
    product_code UNIQUE — 중복 등록 방지.

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

    specs = Column(
        JSONB,
        nullable=True,
        comment=(
            "제품 규격(JSONB). "
            "예: {\"무게\": \"650kg\", \"용량\": \"100Ah\", \"전압\": \"3.7V\"}"
        ),
    )

    # ------------------------------------------------------------------
    # 타임스탬프
    # ------------------------------------------------------------------
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
    bom_versions: Mapped[List["BomVersion"]] = relationship(
        "BomVersion",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Product code={self.product_code!r} name={self.product_name!r}>"


# ---------------------------------------------------------------------------
# 2. BomVersion
# ---------------------------------------------------------------------------

class BomVersion(Base):
    """
    제품 BOM 버전 관리.

    같은 제품도 시점별로 다른 BOM을 가질 수 있음.

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

    effective_from = Column(
        Date,
        nullable=True,
        comment="이 BOM 버전 적용 시작일",
    )

    effective_to = Column(
        Date,
        nullable=True,
        comment="이 BOM 버전 적용 종료일. NULL이면 현재 유효.",
    )

    # ------------------------------------------------------------------
    # 상태 — BomVersionStatus Enum 적용
    # [핵심] nullable=False, default='draft'
    #        직접 UPDATE 금지 — state_machine.py 경유 필수
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
    # 승인 정보 — 타 도메인 FK (User/Auth Domain), relationship 미선언
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
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
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
            f"version={self.version_number!r} status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# 3. Part
# ---------------------------------------------------------------------------

class Part(Base):
    """
    부품 마스터 — Pack→Module→Cell→전구체→광물 5계층 자기참조 트리.

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

    [도메인 격리]
    이 테이블에는 타 도메인 FK 없음. 모든 relationship 정상 선언.
    """

    __tablename__ = "parts"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    part_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="부품 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 식별·명칭
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 5계층 트리 — 계층 레벨
    # 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물
    # ------------------------------------------------------------------
    tier_level = Column(
        Integer,
        nullable=True,
        comment="부품 계층 레벨. 1=Pack / 2=Module / 3=Cell / 4=전구체 / 5=광물",
    )

    # ------------------------------------------------------------------
    # 자기참조 FK — 5계층 트리의 핵심
    # NULL이면 루트(Pack). 광물(tier_level=5)은 자식 없음.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # FTA 필수 — HS Code
    # 6자리 미만 입력 시 Service 레이어에서 HTTP 422 반환
    # ------------------------------------------------------------------
    hs_code = Column(
        String(15),
        nullable=True,
        comment=(
            "HS Code (6자리 이상 필수). "
            "FTA 세번변경기준(CTC2/CTC4/CTC6) 판정 키. "
            "6자리 미만 → API 422."
        ),
    )

    # ------------------------------------------------------------------
    # 소재·기능
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 단가 — RVC 부가가치기준 FTA 판정 계산 입력값
    # ------------------------------------------------------------------
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
        comment="부품 규격(JSONB). 예: {\"용량\": \"100Ah\", \"전압\": \"3.7V\"}",
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # 자기참조 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
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
        remote_side="Part.part_id",  # 문자열로 지정 — 전방 참조 안전
        lazy="select",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
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

    [FTA 관련]
    - origin_country: FTA 원산지 기준 판정 입력값 (ISO 3166-1 alpha-2).
    - direct_material_cost: RVC 계산 시 역내 부가가치 산정 기준.

    [도메인 격리]
    타 도메인 FK 없음. 모든 relationship 정상 선언.
    """

    __tablename__ = "bom_items"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    bom_item_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="BOM 항목 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 FK
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 소요량
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 비용 — RVC 계산 입력값
    # ------------------------------------------------------------------
    direct_material_cost = Column(
        Numeric(15, 4),
        nullable=True,
        comment=(
            "직접재료비. "
            "RVC 계산 시 역내 부가가치 산정 기준. "
            "NULL이면 RVC 계산 불가."
        ),
    )

    # ------------------------------------------------------------------
    # 원산지 — FTA 판정 입력값
    # ISO 3166-1 alpha-2
    # ------------------------------------------------------------------
    origin_country = Column(
        String(2),
        nullable=True,
        comment=(
            "원산지 국가 코드 (ISO 3166-1 alpha-2). "
            "FTA 원산지 기준 판정 입력값."
        ),
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
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

    협력사가 내부적으로 다른 부품 코드를 사용해도 동일 부품으로 추적 가능.
    supply_chain_map의 po_number와 연계해 실물 거래 추적.

    [도메인 격리]
    supplier_id → suppliers.supplier_id
      ForeignKey 문자열 참조만 선언. relationship 없음.
    """

    __tablename__ = "part_code_mapping"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    mapping_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="매핑 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 FK
    # ------------------------------------------------------------------
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id", ondelete="CASCADE"),
        nullable=False,
        comment="원청 기준 부품 FK",
    )

    # ------------------------------------------------------------------
    # 타 도메인 FK (Supplier Domain) — relationship 미선언
    # ------------------------------------------------------------------
    supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=True,
        comment=(
            "협력사 FK → suppliers.supplier_id. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    # ------------------------------------------------------------------
    # 코드 매핑
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
    part: Mapped["Part"] = relationship(
        "Part",
        back_populates="part_code_mappings",
    )

    def __repr__(self) -> str:
        return (
            f"<PartCodeMapping part_id={self.part_id!r} "
            f"supplier_part_code={self.supplier_part_code!r} "
            f"original_part_code={self.original_part_code!r}>"
        )


# ---------------------------------------------------------------------------
# 6. ManufacturingProcess
# ---------------------------------------------------------------------------

class ManufacturingProcess(Base):
    """
    부품별 제조 공정도.

    is_outsourced=True이면 outsourced_to_supplier_id 필수.
    CSDDD·LKSG 실사 시 공정 투명성 증빙에 사용.

    [도메인 격리]
    outsourced_to_supplier_id → suppliers.supplier_id
      is_outsourced=True일 때 필수이나,
      ForeignKey 문자열 참조만 선언. relationship 없음.
      아웃소싱 협력사 정보가 필요한 경우 Service 계층 JOIN 쿼리로 처리.

    [유효성 검사]
    is_outsourced=True이고 outsourced_to_supplier_id=NULL인 경우
    Service 레이어에서 HTTP 422 반환.
    이 모델은 컬럼 정의만 담당.
    """

    __tablename__ = "manufacturing_process"

    # ------------------------------------------------------------------
    # PK
    # ------------------------------------------------------------------
    process_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
        comment="공정 고유 식별자",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 FK
    # ------------------------------------------------------------------
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.part_id", ondelete="CASCADE"),
        nullable=False,
        comment="해당 부품 FK",
    )

    # ------------------------------------------------------------------
    # 공정 정보
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 아웃소싱 여부
    # is_outsourced=True → outsourced_to_supplier_id 필수
    # (Service 레이어에서 검증)
    # ------------------------------------------------------------------
    is_outsourced = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="아웃소싱 공정 여부. True이면 outsourced_to_supplier_id 필수.",
    )

    # ------------------------------------------------------------------
    # 타 도메인 FK (Supplier Domain) — relationship 미선언
    # ------------------------------------------------------------------
    outsourced_to_supplier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.supplier_id"),
        nullable=True,
        comment=(
            "아웃소싱 대상 협력사 FK → suppliers.supplier_id. "
            "is_outsourced=True일 때 필수. "
            "도메인 격리 원칙: 문자열 FK만 선언, relationship 없음."
        ),
    )

    # ------------------------------------------------------------------
    # 공정도 이미지
    # DPP 발행 시 첨부 및 규제 당국 제출용
    # ------------------------------------------------------------------
    process_image_url = Column(
        String(500),
        nullable=True,
        comment="제조 공정도 이미지 URL. DPP 발행 시 첨부 및 규제 당국 제출용.",
    )

    # ------------------------------------------------------------------
    # 도메인 내부 Relationships — Mapped[] 적용 (SQLAlchemy 2.0)
    # ------------------------------------------------------------------
    part: Mapped["Part"] = relationship(
        "Part",
        back_populates="manufacturing_processes",
    )

    def __repr__(self) -> str:
        return (
            f"<ManufacturingProcess part_id={self.part_id!r} "
            f"seq={self.sequence_no!r} "
            f"name={self.process_name!r} "
            f"outsourced={self.is_outsourced!r}>"
        )


# ============================================================
# [2] Pydantic 입출력 스키마(DTO) 영역
# ============================================================

class ProductCreateRequest(BaseModel):
    """제품 등록 요청 바디. product_code UNIQUE — 중복 시 409."""
    product_code: str
    product_name: Optional[str] = None
    manufacturer_id: Optional[uuid.UUID] = None
    type: Optional[str] = None
    specs: Optional[Dict[str, Any]] = None


class ProductBrief(BaseModel):
    """
    목록·단건 응답용 직렬화 스키마.
    ORM 객체를 그대로 반환하면 relationship lazy load에서 직렬화 에러가
    날 수 있으므로, 명시적 스키마로 변환해 반환한다(직렬화 안전).
    from_attributes=True 로 ORM 인스턴스에서 바로 만든다.
    """
    product_id: uuid.UUID
    product_code: str
    product_name: Optional[str] = None
    type: Optional[str] = None
    manufacturer_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class BomTreeResponse(BaseModel):
    """
    BOM 트리 응답 스키마.
    active BOM 버전이 없으면 service에서 404 반환.
    """
    product_id: uuid.UUID
    product_code: str
    bom_version: str
    bom_status: str
    tree: Dict[str, Any]

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
