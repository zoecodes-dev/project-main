import uuid
from typing import Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_node
from backend.domains.dpp.repository import get_readiness_metrics, get_score_raw_data


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
        # schema.sql의 compliance_results.verdict 기준 상태값 매핑 ('passed', 'gray_zone', 'violation')
        passed = raw_data["compliance"].get("passed", 0)
        warning = raw_data["compliance"].get("gray_zone", 0)
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
    # 찾아주신 CBAM 5대 섹션 80개 필드를 JSON 계층 구조로 완벽하게 매핑했어요.
    # 1대/2대/3대 산식 변수와 결과값이 정확한 위치에 들어가도록 주석을 달아두었습니다.
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
            "20_goods_description": raw_data.get("part_name", "TODO"),
            "21_country_of_origin": raw_data["destination"],
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
            "64_accompanying_documents_identifier": "TODO",
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
        "readiness_breakdown": readiness_breakdown,
        "scores": {
            "esg_compliance": esg_score,
            "traceability_coverage": traceability_score,
            "reliability": reliability_score
        },
        # 기존 annex_xiii_fields 키를 유지하되, 찾아주신 CBAM 80필드 뼈대를 담습니다.
        "annex_xiii_fields": cbam_80_fields
    }