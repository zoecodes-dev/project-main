"""
domains/supplychain/service.py  (담당: 팀원 D · 영수)

공급망 비즈니스 로직. 이벤트 발행은 반드시 infrastructure 계층 경유.
직접 redis import 금지 (수정됨) → event_bus.publish() + queue.enqueue() 사용.
도메인 간 직접 import 금지 → events/types.py의 dataclass로만 통신.
"""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.types import GeoRiskDetectedEvent
from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node
from backend.domains.supplychain.repository import SupplyChainRepository


class SupplyChainCycleError(ValueError):
    """순환 참조를 만드는 공급망 관계 등록 시도."""


class SupplyRatioExceededError(ValueError):
    """공급 비율 합이 100을 초과."""


class SupplyChainService:
    def __init__(self, repository: SupplyChainRepository):
        self.repository = repository

    # ---------- 공급망 그래프 ----------
    async def get_supply_tree(self, product_id: str) -> List[Dict[str, Any]]:
        """product_id 기준 N차 공급망 트리 조회."""
        return await self.repository.get_n_tier_supply_chain(product_id)

    async def register_relation(
        self,
        bom_version_id: str,
        parent_supplier_id: str | None,
        child_supplier_id: str,
        part_id: str,
    ) -> Dict[str, Any]:
        """
        공급망 관계 등록. 스펙 5-1 유효성 검증:
        1. parent == child 면 거부
        2. 순환 참조 사전 검사 (재귀 CTE)
        """
        if parent_supplier_id is not None and parent_supplier_id == child_supplier_id:
            raise ValueError("parent_supplier_id와 child_supplier_id가 동일할 수 없습니다.")

        if parent_supplier_id is not None:
            if await self.repository.would_create_cycle(
                parent_supplier_id, child_supplier_id
            ):
                raise SupplyChainCycleError(
                    "해당 관계는 공급망에 순환 참조를 발생시킵니다."
                )

        result = await self.repository.create_supply_relation(
            bom_version_id, parent_supplier_id, child_supplier_id, part_id
        )
        await self.repository.session.commit()
        return result

    async def get_alternatives(
        self, product_id: str, part_id: str
    ) -> List[Dict[str, Any]]:
        return await self.repository.get_alternatives(product_id, part_id)

    async def get_by_bom_depth(self, bom_depth: int) -> List[Dict[str, Any]]:
        """부품 tier(bom_depth, 0-base) 기준 공급망 노드 횡단 조회."""
        return await self.repository.get_by_bom_depth(bom_depth)

    async def get_by_hop(self, hop_level: int) -> List[Dict[str, Any]]:
        """공급망 차수(hop_level, 원청 0 기준 경로 순번) 기준 노드 횡단 조회."""
        return await self.repository.get_by_hop(hop_level)

    # ---------- 협력사 통지 및 자진신고 (회사 경계 의무) ----------
    @trace_node("notify_supplier_correction", "agent")
    async def request_supplier_correction(
        self,
        sender_id: str,
        target_supplier_id: str,
        reason: str,
        due_date: str,
        required_docs: list[str]
    ) -> Dict[str, Any]:
        """원청 → 협력사 반려/시정요청 통지. 회사 경계를 넘을 때만 유효함."""
        boundary_check = await self.evaluate_cross_entity_boundary(sender_id, target_supplier_id)
        if not boundary_check.get("is_cross_boundary"):
            raise ValueError(f"동일 법인 내부이거나 통지 대상이 아닙니다. (사유: {boundary_check.get('reason')})")

        payload = {
            "sender_supplier_id": sender_id,
            "target_supplier_id": target_supplier_id,
            "reason": reason,
            "due_date": due_date,
            "required_documents": required_docs,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "sent_by": sender_id,
        }
        # 알림/요청 저장은 notification_worker 등 수신 후 처리
        await publish("supplier.notification_sent", payload)
        
        return {
            "status": "success", 
            "message": "협력사 시정 요청 통지 이벤트가 발행되었습니다.",
            "delivery_record": {
                "sent_by": payload["sent_by"],
                "sent_at": payload["sent_at"],
                "target_supplier_id": payload["target_supplier_id"]
            }
        }

    @trace_node("declare_source_change", "agent")
    async def declare_source_change(
        self,
        bom_version_id: str,
        parent_supplier_id: str,
        new_child_supplier_id: str,
        part_id: str,
        reason: str
    ) -> Dict[str, Any]:
        """협력사 자진신고: 공급원 변경 (사후 적발 방지)"""
        if await self.repository.would_create_cycle(parent_supplier_id, new_child_supplier_id):
            raise SupplyChainCycleError("해당 관계는 공급망에 순환 참조를 발생시킵니다.")

        new_map = await self.repository.declare_new_source(
            bom_version_id, parent_supplier_id, new_child_supplier_id, part_id
        )
        await self.repository.session.commit()

        # 자진신고 발생 시, 상위 BOM 검증을 위해 이벤트 발행 (Compliance/Verification 트리고)
        payload = {
            **new_map, 
            "reason": reason,
            "declared_at": datetime.now(timezone.utc).isoformat(),
            "requires_full_revalidation": True  # 상위 BOM 영향에 따른 재검증 트리거 신호
        }
        await publish("supplier.source_change_declared", payload)
        return {
            "status": "success",
            "message": "공급원 변경 자진신고가 접수되어 상위 파이프라인 재검증이 트리거되었습니다.",
            "data": payload
        }

    # [REVERT-NON-SUPPLIER:BEGIN] 협력사 확인(verify) 상태 갱신 — supply_chain_map.verification_status.
    #   supplier 외(supplychain) 도메인. 최종 작업 시 이 메서드 전체 주석/삭제.
    async def set_supplier_verification(
        self,
        bom_version_id: str,
        supplier_id: str,
        verified: bool,
    ) -> Dict[str, Any]:
        """STEP3 협력사 '확인' — 해당 협력사 맵 엣지의 verification_status를 verified/unverified로."""
        updated = await self.repository.set_supplier_verification(bom_version_id, supplier_id, verified)
        await self.repository.session.commit()
        return {
            "bom_version_id": bom_version_id,
            "supplier_id": supplier_id,
            "verification_status": "verified" if verified else "unverified",
            "updated_edges": updated,
        }
    # [REVERT-NON-SUPPLIER:END]

    async def get_gaps(self, product_id: str) -> Dict[str, Any]:
        """
        C2 맵 gap 계산: 제품 공급망 노드별로 규제 필수 필드 중 미보유 항목 목록 반환.

        흐름:
          1. repository에서 공급망 협력사 + 필드 보유 현황 조회
          2. regulation service 스텁에서 적용 규제 + 규제별 필수 필드 조회
          3. 협력사 provider_type × provider_type_applicable 교차 → missing 계산
          4. 노드별 gap 목록 반환
        """
        from backend.domains.regulation.service import (
            get_applicable_regulations,
            get_required_fields,
        )

        # 필드명 → 보유 여부 컬럼 매핑 (repository 반환 컬럼명과 1:1 대응)
        FIELD_HAS_MAP: Dict[str, str] = {
            "carbon_intensity":          "has_carbon_intensity",
            "factory_carbon_declarations": "has_factory_carbon_decl",
            "recycled_content_ratio":    "has_recycled_content_ratio",
            "recycled_materials":        "has_recycled_materials",
            "mine_coordinates":          "has_mine_coordinates",
            "origin_country":            "has_origin_country",
            "feoc_direct_ownership":     "has_feoc_direct_ownership",
            "feoc_indirect_ownership":   "has_feoc_indirect_ownership",
            # geo_risk_flags: 지오 감사에서 실시간 계산 — 항상 보유로 간주
        }

        supplier_rows = await self.repository.get_supplier_field_data(product_id)
        if not supplier_rows:
            return {"product_id": product_id, "nodes": []}

        db = self.repository.session
        regulations = await get_applicable_regulations(db, product_id)
        # 규제별 필수 필드 미리 로드
        reg_fields: Dict[str, List[Dict]] = {}
        for reg in regulations:
            reg_fields[reg["regulation_id"]] = await get_required_fields(db, reg["regulation_code"])

        nodes = []
        for row in supplier_rows:
            provider_type = row["provider_type"]
            missing: List[Dict[str, str]] = []

            for reg in regulations:
                for field in reg_fields.get(reg["regulation_id"], []):
                    applicable_types = field.get("provider_type_applicable") or []
                    if applicable_types and provider_type not in applicable_types:
                        continue  # 이 협력사 유형에 해당 없는 필드
                    if not field.get("is_mandatory"):
                        continue  # 선택 필드는 gap으로 집계하지 않음

                    has_col = FIELD_HAS_MAP.get(field["field_name"])
                    if has_col is None:
                        continue  # 매핑 없는 필드(geo_risk_flags 등) 스킵
                    if not row.get(has_col):
                        missing.append({
                            "field_name":       field["field_name"],
                            "field_label":      field.get("field_label", ""),
                            "regulation_code":  reg["regulation_code"],
                            "regulation_name":  reg["name"],
                        })

            nodes.append({
                "supplier_id":    str(row["supplier_id"]),
                "provider_type":  provider_type,
                "depth":          row["depth"],
                "is_root_anchor": bool(row.get("is_root_anchor", False)),
                "missing_fields": missing,
                "gap_count":      len(missing),
            })

        return {"product_id": product_id, "nodes": nodes}

    async def get_geo_risks(self, db: AsyncSession) -> Dict[str, Any]:
        """
        조회 전용 인터페이스: 이벤트를 발행하지 않고 지정학 리스크 결과를 반환합니다.
        """
        xinjiang_risks = await self.repository.check_geo_audit_risk_zone()
        mismatch_risks = await self.repository.check_coordinate_authenticity(db)
        eudr_risks = await self.repository.check_eudr_deforestation(db)
        
        return {
            "xinjiang_adjacent": xinjiang_risks,
            "country_mismatch": mismatch_risks,
            "eudr_deforestation": eudr_risks
        }

    # ---------- 공급망 맵 (10.2a / 10.2b) ----------

    async def get_supply_chain_map(
        self,
        product_id: str,
        tenant_id: str,
        bom_version_id: str | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
        factory_id: str | None = None,
        po_number: str | None = None,
    ) -> dict:
        return await self.repository.get_supply_chain_map(
            product_id=product_id,
            tenant_id=tenant_id,
            bom_version_id=bom_version_id,
            period_from=period_from,
            period_to=period_to,
            factory_id=factory_id,
            po_number=po_number,
        )

    async def confirm_supply_chain_map(
        self, map_id: str, tenant_id: str
    ) -> dict | None:
        result = await self.repository.confirm_map(map_id, tenant_id)
        if result is None:
            return None
        await self.repository.session.commit()
        # 응답 계약(스펙 10.2b): status는 link_status enum 원본이 아니라 "confirmed" 고정값.
        # DB에는 link_status='supplychain_confirmed'로 저장되지만 프론트 계약은 {mapId, status:"confirmed"}.
        return {"map_id": result["map_id"], "status": "confirmed"}

    # [REVERT-NON-SUPPLIER:BEGIN] supplier 외(supplychain) — 공급망 맵 헤더(맵 그 자체) 관리.
    async def list_maps(self, tenant_id: str) -> List[Dict[str, Any]]:
        """내 테넌트의 공급망 맵 목록(map_id 단위)."""
        return await self.repository.list_map_headers(tenant_id)

    async def get_map(self, map_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """공급망 맵 단건(map_id). 소유 테넌트만."""
        return await self.repository.get_map_header(map_id, tenant_id)

    async def set_map_status(self, map_id: str, status: str, user_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """맵 완료/전송 상태 변경 후 커밋."""
        result = await self.repository.set_map_status(map_id, status, user_id, tenant_id)
        if result is None:
            return None
        await self.repository.session.commit()
        return result
    # [REVERT-NON-SUPPLIER:END]

    async def get_hitl_geo_context(self, db: AsyncSession) -> Dict[str, Any]:
        """
        HITL 검토 화면용 조회 유틸리티.
        차윤(E)이 다루기 쉽도록 반환되는 GeoJSON 좌표를 단순한 [latitude, longitude] 배열로 포장하고,
        '신장 50km 이내', '신고국 불일치' 등의 회색지대(Gray Zone) 판단 결과를 함께 제공합니다.
        """
        xinjiang_risks = await self.repository.check_geo_audit_risk_zone()
        mismatch_risks = await self.repository.check_coordinate_authenticity(db)
        eudr_risks = await self.repository.check_eudr_deforestation(db)

        def _format_risk_items(risk_list: List[Dict[str, Any]], risk_type: str) -> List[Dict[str, Any]]:
            formatted = []
            for r in risk_list:
                item = {
                    "factory_id": str(r["factory_id"]),
                    "supplier_id": str(r["supplier_id"]),
                    "company_name": r["company_name"],
                    "coordinates": self.parse_geojson_to_latlng(r.get("coordinates")),
                    "is_gray_zone": False
                }
                
                # 회색지대(Gray Zone) 플래그 및 경고 메시지 세팅
                if risk_type == "xinjiang":
                    item["is_in_risk_zone"] = r.get("is_in_risk_zone")
                    item["distance_km"] = float(r["distance_km"]) if r.get("distance_km") is not None else None
                    if item["is_in_risk_zone"]:
                        item["is_gray_zone"] = True
                        item["gray_zone_warning"] = "신장 지역 50km 이내 인접 (위험구역)"
                elif risk_type == "country_mismatch":
                    item["country"] = r.get("country")
                    item["country_match"] = r.get("country_match")
                    if not item["country_match"]:
                        item["is_gray_zone"] = True
                        item["gray_zone_warning"] = f"신고 국가({item.get('country')})와 실제 좌표 불일치"
                elif risk_type == "eudr":
                    item["is_deforested"] = r.get("is_deforested")
                    if item["is_deforested"]:
                        item["is_gray_zone"] = True
                        item["gray_zone_warning"] = "EUDR 산림 훼손 의심 지역 내부 위치"
                        
                formatted.append(item)
            return formatted

        return {
            "factory_gps": {
                "xinjiang_adjacent": _format_risk_items(xinjiang_risks, "xinjiang"),
                "country_mismatch": _format_risk_items(mismatch_risks, "country_mismatch"),
                "eudr_deforestation": _format_risk_items(eudr_risks, "eudr")
            }
        }

    async def evaluate_cross_entity_boundary(
        self, requester_supplier_id: str, target_supplier_id: str
    ) -> Dict[str, Any]:
        """
        회사 경계(Legal Entity) 검증 룰:
        원청사 또는 상위 협력사(requester)에서 하위 협력사(target)로 데이터를 요청할 때,
        두 협력사가 동일 법인 내부에 속하는지, 회사 경계를 넘는 외부 거래인지 판별합니다.
        회사 경계를 넘을 경우 '통지' 및 '자진신고(Self-Declaration)' 의무를 부여합니다. (사후적발 방지 핵심)
        """
        if requester_supplier_id == target_supplier_id:
            return {
                "is_cross_boundary": False,
                "requires_self_declaration": False,
                "reason": "동일 협력사 내부 요청 (의무 없음)"
            }

        is_cross = await self.repository.is_cross_company_boundary(
            requester_supplier_id, target_supplier_id
        )

        return {
            "is_cross_boundary": is_cross,
            "requires_self_declaration": is_cross,
            "reason": "외부 법인 경계 횡단 (통지 및 자진신고 의무 발생)" if is_cross else "동일 법인 내부 이동 (회사 경계 의무 없음)"
        }

    # ---------- Geo Audit ----------
    def parse_geojson_to_latlng(self, geojson_str: str | None) -> list[float]:
        """
        PostGIS 등에서 반환된 GeoJSON 문자열을 파싱하여,
        프론트엔드 및 HITL 화면에서 즉시 맵에 핀을 꽂기 쉽도록 정형화된 [latitude, longitude] 배열로 반환합니다.
        """
        if not geojson_str:
            return []
        try:
            geo = json.loads(geojson_str)
            if geo.get("type") == "Point" and "coordinates" in geo:
                lon, lat = geo["coordinates"]
                return [lat, lon]
        except Exception:
            pass
        return []

    @trace_node("geo_audit_execute", "agent")
    async def execute_geo_audit(self, db: AsyncSession, batch_id: str | None = None) -> List[Dict[str, Any]]:
        """
        공장 위치 기반 Geo Audit 수행. 고위험 지역(신장 등) 판정 시
        GeoRiskDetected 이벤트를 발행한다.
        db 인자는 @trace_node가 audit_trail 기록에 사용.
        """
        audit_results = await self.repository.check_geo_audit_risk_zone()
        mismatch_results = await self.repository.check_coordinate_authenticity(db)
        eudr_results = await self.repository.check_eudr_deforestation(db)

        detected_risks: List[Dict[str, Any]] = []
        for result in audit_results:
            if result.get("is_in_risk_zone"):
                formatted_coords = self.parse_geojson_to_latlng(result["coordinates"])
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="xinjiang",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=formatted_coords,
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        for result in mismatch_results:
            if not result.get("country_match"):
                formatted_coords = self.parse_geojson_to_latlng(result["coordinates"])
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="country_mismatch",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=formatted_coords,
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        for result in eudr_results:
            if result.get("is_deforested"):
                formatted_coords = self.parse_geojson_to_latlng(result.get("coordinates"))
                event = GeoRiskDetectedEvent(
                    batch_id=batch_id,
                    factory_id=result["factory_id"],
                    risk_type="eudr_deforestation",
                    supplier_id=result["supplier_id"],
                    company_name=result["company_name"],
                    coordinates=formatted_coords,
                )
                await self._publish_geo_risk(event)
                detected_risks.append(asdict(event))

        return detected_risks

    async def _publish_geo_risk(self, event: GeoRiskDetectedEvent) -> None:
        """
        GeoRiskDetected 이벤트 발행 (후속 처리는 risk_scoring 노드가 인라인 처리)
        """
        payload = asdict(event)
        await publish(event.event_name, payload)
