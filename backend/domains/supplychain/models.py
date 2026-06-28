# =============================================================================
# backend/domains/supplychain/models.py
#
# KIRA Compliance Intelligence Platform — SupplyChain Domain ORM Models
#
# 구현 대상: schema.sql 영역 8 (공급망 맵)
#   - supply_chain_map, supply_ratio
#
# 설계 원칙 (PROJECT_CORE.md 5-1 ~ 5-5 준수):
#   1. 도메인 격리 — 타 도메인(Supplier, Product) 모델 클래스 import 금지.
#      ForeignKey("테이블.컬럼") 문자열로만 선언하고 relationship() 미선언.
#   2. N차 탐색 우회 — 본 모델은 단순 CRUD 및 직렬화용이며,
#      N차 추적은 repository.py의 재귀 CTE(raw SQL)를 유지함.
# =============================================================================

from __future__ import annotations

import uuid
from typing import List

from sqlalchemy import Column, Date, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP

from backend.infrastructure.database import Base


# ---------------------------------------------------------------------------
# 0. SupplyChainMaps — 공급망 맵 헤더(맵 그 자체). 엣지(supply_chain_map)를 묶는 1급 엔티티.
# [MARKER:BEGIN] supplier 외(supplychain) — 맵 헤더 엔티티 신설.
# ---------------------------------------------------------------------------
# class SupplyChainMaps(Base):
#     """공급망 맵 1개 = map_id 1개 = bom_version(제품×Lot) 1개. 완료/전송 상태 관리."""
#     __tablename__ = "supply_chain_maps"
#
#     map_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
#     bom_version_id = Column(UUID(as_uuid=True), ForeignKey("bom_versions.bom_version_id"))
#     product_id = Column(UUID(as_uuid=True), ForeignKey("products.product_id"))
#     status = Column(String(20), server_default="building", comment="building / completed")
#     completed_by = Column(UUID(as_uuid=True), ForeignKey("users.user_id"))
#     completed_at = Column(TIMESTAMP(timezone=True))
#     created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
# [MARKER:END]


# ---------------------------------------------------------------------------
# 1. SupplyChainMap
# ---------------------------------------------------------------------------
class SupplyChainMap(Base):
    """
    N차 전방 공급망 흐름의 그래프 연결 대장.
    """
    __tablename__ = "supply_chain_map"

    # [MARKER:BEGIN] supplier 외(supplychain) — map_id(엣지 PK)를 edge_id로 개명하고
    #   map_id 는 맵 헤더(supply_chain_maps) FK 로 재정의. (한 줄=1 엣지, map_id=소속 맵)
    # edge_id = Column(
    #     UUID(as_uuid=True),
    #     primary_key=True,
    #     server_default=func.uuid_generate_v4(),
    # )
    # map_id = Column(UUID(as_uuid=True), ForeignKey("supply_chain_maps.map_id"), nullable=True)
    # [MARKER:END]
    bom_version_id = Column(UUID(as_uuid=True), ForeignKey("bom_versions.bom_version_id"))
    parent_supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"))
    child_supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.part_id"))
    
    po_number = Column(String(50))
    invoice_number = Column(String(50))
    supply_period_from = Column(Date)
    supply_period_to = Column(Date)
    
    link_status = Column(
        String(30),
        server_default="supplychain_declared",
        comment="상태: supplychain_declared / supplychain_confirmed"
    )
    discovered_via = Column(UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"))
    source_system = Column(
        String(50),
        server_default="ERP",
        comment="출처: ERP / SUPPLIER_DECLARED"
    )
    verification_status = Column(
        String(20),
        server_default="unverified",
        comment="검증: unverified / verified"
    )
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    # 도메인 내부 Relationship
    ratios: Mapped[List["SupplyRatio"]] = relationship(
        "SupplyRatio",
        back_populates="supply_chain_map",
        cascade="all, delete-orphan",
        lazy="select",
    )


# ---------------------------------------------------------------------------
# 2. SupplyRatio
# ---------------------------------------------------------------------------
class SupplyRatio(Base):
    """공동 납품 시 공장별 분할 기여도 관리 대장."""
    __tablename__ = "supply_ratio"

    ratio_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4())
    # [MARKER] supplier 외(supplychain) — FK를 supply_chain_map.edge_id 로 개명
    edge_id = Column(UUID(as_uuid=True), ForeignKey("supply_chain_map.edge_id", ondelete="CASCADE"), nullable=False)
    factory_id = Column(UUID(as_uuid=True), ForeignKey("supplier_factories.factory_id"))
    ratio_percentage = Column(Numeric(5, 2))
    volume = Column(Numeric(15, 4))
    unit = Column(String(20))

    supply_chain_map: Mapped["SupplyChainMap"] = relationship(
        "SupplyChainMap",
        back_populates="ratios",
    )