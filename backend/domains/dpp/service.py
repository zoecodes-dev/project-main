import uuid
import dataclasses
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.event_bus import publish
from backend.infrastructure.trace import trace_node, trace_tool
from backend.domains.dpp.repository import get_readiness_metrics, get_score_raw_data
from backend.events.types import DPPReadinessUpdatedEvent
from backend.domains.dpp.models import DppRecord


# ==============================================================================
# 서비스 로직 (Verification Engine)
# ==============================================================================

@trace_node("calculate_readiness", node_type="agent")
async def calculate_readiness(
    db: AsyncSession,
    product_id: uuid.UUID
) -> Dict[str, Any]:
    """
    [Verification Engine]
    제품의 발행 준비도(Readiness) 8대 항목을 평가하고 이벤트를 방출해요.
    """
    breakdown = await get_readiness_metrics(db, product_id)
    
    # 8개 항목 중 True인 개수를 구해서 0.0 ~ 1.0 점수를 매깁니다. (전부 충족 시 1.0)
    passed_count = sum(1 for passed in breakdown.values() if passed)
    score = round(passed_count / len(breakdown), 2) if breakdown else 0.0

    event = DPPReadinessUpdatedEvent(
        product_id=product_id,
        readiness_score=score,
        readiness_breakdown=breakdown
    )
    
    await publish("DPPReadinessUpdated", dataclasses.asdict(event))
    
    return {"product_id": product_id, "readiness_score": score, "breakdown": breakdown}


# 목적지별 적용 규제 수 (agents/compliance.py의 REGULATION_BY_DESTINATION 기준)
REGULATION_COUNT = {
    "EU": 8,
    "US": 3,
    "KR": 0,
    "BOTH": 10
}

# 자가평가 비교를 위한 리스크 계층 맵핑
RISK_WEIGHTS = {"low": 0, "medium": 1, "high": 2, "critical": 3, "unknown": 0}


@trace_node("generate_dpp_payload", node_type="agent")
async def generate_dpp_payload(
    db: AsyncSession,
    product_id: uuid.UUID,
    batch_id: uuid.UUID
) -> Dict[str, Any]:
    """
    [Verification Engine]
    제품 정보, 컴플라이언스 결과, 공급망 점수를 결합하여
    Annex XIII 80필드 규격의 JSON(DPP Payload)을 생성해요.
    """
    # 1. 항목별 충족 여부 (Readiness Breakdown) 기록
    readiness_breakdown = await get_readiness_metrics(db, product_id)

    # 2. 3대 점수 산식을 위한 Raw 데이터 수집
    raw_data = await get_score_raw_data(db, batch_id)

    # =====================================================================
    # ① ESG Compliance Score (%)
    # =====================================================================
    destination = raw_data["destination"]
    total_regulations = REGULATION_COUNT.get(destination, 0)

    if total_regulations == 0:
        esg_score = 100.0
    else:
        # schema.sql의 compliance_results.verdict ('compliance_passed', 'compliance_warning' 등) 값을 집계해요.
        passed = raw_data["compliance"].get("compliance_passed", 0)
        warning = raw_data["compliance"].get("compliance_warning", 0)
        esg_score = round(((passed + warning * 0.5) / total_regulations) * 100, 2)

    # =====================================================================
    # ② Traceability Coverage (%)
    # =====================================================================
    trace_total = raw_data["traceability"]["total"]
    trace_approved = raw_data["traceability"]["approved"]
    traceability_score = round((trace_approved / trace_total * 100), 2) if trace_total > 0 else 0.0

    # =====================================================================
    # ③ Reliability Score (0~100) — 공급망 전체 평균
    # =====================================================================
    reliability_scores = []
    for sup in raw_data["suppliers"]:
        comp_part = sup["completeness"] * 0.4
        sla_part = max(30 - (sup["overdue_count"] * 5), 0)

        # 자가평가 성실도 (팀원 B가 DDL을 배포하기 전까지는 안전하게 'low'로 Fallback)
        self_risk = sup.get("self_reported_risk_level", "low")
        diff = RISK_WEIGHTS.get(sup["risk_level"], 0) - RISK_WEIGHTS.get(self_risk, 0)

        if diff <= 0: self_part = 30
        elif diff == 1: self_part = 20
        elif diff == 2: self_part = 10
        else: self_part = 0

        reliability_scores.append(comp_part + sla_part + self_part)

    reliability_score = round(sum(reliability_scores) / len(reliability_scores), 2) if reliability_scores else 0.0

    # =====================================================================
    # ④ 규제 당국 제출용 80개 필수 필드 (CBAM 전환기 보고서 규격)
    # =====================================================================
    cbam_80_fields = {
        "section_1_report_and_declarant": {
            "01_report_id": str(uuid.uuid4()),
            "02_reporting_period": "2026-Q1",
            "03_year": 2026,
            "04_submission_status": "Draft",
            "05_customs_declarant_eori": raw_data.get("business_reg_no", "TODO"),
            "06_customs_declarant_name": raw_data.get("tenant_company_name", "KIRA OEM Inc."),
            "07_customs_declarant_country": raw_data.get("tenant_country", "KR"),
            "08_importer_eori": raw_data.get("business_reg_no", "TODO"),
            "09_importer_name": raw_data.get("tenant_company_name", "KIRA OEM Inc."),
            "10_importer_country": raw_data.get("tenant_country", "KR"),
            "11_representative_eori": "TODO",
            "12_representative_name": "TODO",
            "13_representative_role": "TODO",
            "14_contact_person_name": raw_data.get("contact_name", "TODO"),
            "15_contact_person_email_phone": raw_data.get("contact_email", "TODO")
        },
        "section_2_customs_and_goods": {
            "16_customs_declaration_number_mrn": "TODO",
            "17_customs_declaration_date": "TODO",
            "18_customs_office_of_import": "TODO",
            "19_cn_code_of_goods": raw_data.get("hs_code", "TODO"),
            "20_goods_description": raw_data.get("product_name") or raw_data.get("part_name") or "TODO",
            "21_country_of_origin": raw_data.get("item_origin") or raw_data.get("destination") or "TODO",
            "22_net_mass": raw_data.get("net_mass", 0.0),  # [3대 산식 변수] 수입 물품 순 중량
            "23_supplementary_units": "TODO",
            "24_commercial_invoice_number": raw_data.get("invoice_number", "TODO"),
            "25_commercial_invoice_date": "TODO",
            "26_total_invoice_value": raw_data.get("unit_price", 0.0),
            "27_invoice_currency": "EUR",
            "28_terms_of_delivery": "TODO",
            "29_nature_of_transaction": "TODO",
            "30_mode_of_transport": "TODO",
            "31_container_id": "TODO",
            "32_transport_document_number": "TODO",
            "33_economic_operator_name": raw_data.get("supplier_name_en", "TODO"),
            "34_national_customs_procedure_code": "TODO",
            "35_valuation_method": "TODO"
        },
        "section_3_installation_and_process": {
            "36_production_installation_id": str(uuid.uuid4()),
            "37_installation_name": raw_data.get("factory_name_en", "TODO"),
            "38_country_of_installation": raw_data.get("country", "TODO"),
            "39_installation_address": raw_data.get("address", "TODO"),
            "40_geographical_coordinates": raw_data.get("location_wkt", "TODO"),
            "41_operator_name": raw_data.get("company_name_en", "TODO"),
            "42_production_route": raw_data.get("manufacturing_process", "TODO"),
            "43_production_route_description": "TODO",
            "44_activity_level": raw_data.get("volume", 0.0),  # [1대 산식 분모]
            "45_system_boundaries_defined": "TODO",
            "46_direct_emissions_total": 0.0,
            "47_indirect_emissions_total": 0.0,
            "48_biomass_emissions": 0.0,
            "49_heating_steam_emissions": 0.0,
            "50_source_stream_data": "TODO"
        },
        "section_4_embedded_emissions_scores": {
            "51_specific_direct_embedded_emissions": raw_data.get("carbon_intensity", 0.0),  # [3대 산식 결과]
            "52_specific_indirect_embedded_emissions": 0.0,  # [3대 산식 결과]
            "53_determination_methodology_direct": "TODO",
            "54_determination_methodology_indirect": "TODO",
            "55_electricity_consumption_factor": 0.0,
            "56_electricity_source": raw_data.get("energy_source", "TODO"),
            "57_total_electricity_consumed": 0.0,
            "58_specific_embedded_emissions_total": 0.0,
            "59_total_embedded_emissions": raw_data.get("carbon_footprint", 0.0),  # [3대 산식 최종 스코어]
            "60_qualified_verifier_id": "TODO",
            "61_verifier_name": raw_data.get("auditor", "KIRA AI Verification Engine"),
            "62_verification_report_number": "TODO",
            "63_verification_opinion_status": "TODO",
            "64_accompanying_documents_identifier": raw_data.get("certification_no", "TODO"),
            "65_data_qualifying_flags": "TODO"
        },
        "section_5_precursors_and_carbon_price": {
            "66_precursor_type_indicator": "Complex",
            "67_precursor_cn_code": raw_data.get("precursor_hs_code", "TODO"),
            "68_precursor_quantity_consumed": raw_data.get("precursor_quantity", 0.0),  # [2대 산식 변수]
            "69_precursor_specific_direct_emissions": 0.0,
            "70_precursor_specific_indirect_emissions": 0.0,
            "71_precursor_country_of_origin": raw_data.get("precursor_origin", "TODO"),
            "72_carbon_price_paid_indicator": "Y",
            "73_type_of_carbon_pricing_instrument": "TODO",
            "74_country_of_carbon_price": raw_data.get("tenant_country", "KR"),
            "75_amount_of_carbon_price_paid": 0.0,
            "76_currency_of_payment": "KRW",
            "77_quantity_of_covered_emissions": 0.0,
            "78_rebates_allocations_received": 0.0,
            "79_net_carbon_price_paid_score": 0.0,
            "80_carbon_price_supporting_docs": raw_data.get("origin_cert_url", "TODO")
        }
    }

    return {
        "product_info": {
            "customer_id": raw_data.get("customer_id"),
            "customer_name": raw_data.get("customer_name", "Unknown"),
            "model_name": raw_data.get("model_name", "Unknown"),
            "amperage_ah": raw_data.get("amperage_ah", 0.0),
        },
        "readiness_breakdown": readiness_breakdown,
        "scores": {
            "esg_compliance": esg_score,
            "traceability_coverage": traceability_score,
            "reliability": reliability_score
        },
        "annex_xiii_fields": cbam_80_fields
    }


@trace_tool("create_dpp_record")
async def create_dpp_record(
    db: AsyncSession,
    batch_id: uuid.UUID,
    product_id: uuid.UUID,
    carbon_footprint: float,
    qr_code_url: str,
    payload: Dict[str, Any]
) -> uuid.UUID:
    """
    [DPP Service]
    발행 준비가 완료된 DPP 초안 레코드를 생성합니다.
    """
    # DB의 DEFAULT 'dpp_issued' 제약에 걸리지 않도록 status=None을 명시해 줘요.
    # 그래야 assert_not_issued 가드를 무사히 통과하고 issue_dpp에서 확정 지을 수 있어요.
    dpp_record = DppRecord(
        batch_id=batch_id,
        product_id=product_id,
        carbon_footprint=carbon_footprint,
        qr_code_url=qr_code_url,
        payload=payload,
        status=None
    )
    db.add(dpp_record)
    await db.flush()
    
    return dpp_record.dpp_id