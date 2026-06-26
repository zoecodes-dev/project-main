import uuid
import dataclasses
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

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


# [BYPASS:B6] 미수집 필드 None 처리 — 시드 raw_data 보강은 별도(은진)
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
            "05_customs_declarant_eori": raw_data.get("business_reg_no"),              # (a) seed채움
            "06_customs_declarant_name": raw_data.get("tenant_company_name", "KIRA OEM Inc."),
            "07_customs_declarant_country": raw_data.get("tenant_country", "KR"),
            "08_importer_eori": str(raw_data.get("customer_id")) if raw_data.get("customer_id") else None,  # (a) seed채움
            "09_importer_name": raw_data.get("customer_name"),                         # (a) seed채움
            "10_importer_country": raw_data.get("tenant_country", "KR"),
            "11_representative_eori": None,                                            # (b) N/A — KIRA는 직접 신고, 대리인 없음
            "12_representative_name": None,                                            # (b) N/A
            "13_representative_role": None,                                            # (b) N/A
            "14_contact_person_name": raw_data.get("contact_name"),                    # (a) seed채움
            "15_contact_person_email_phone": raw_data.get("contact_email")             # (a) seed채움
        },
        "section_2_customs_and_goods": {
            "16_customs_declaration_number_mrn": None,                                 # (c) Blocker — 통관 신고번호 미수집
            "17_customs_declaration_date": None,                                       # (c) Blocker — 통관일 미수집
            "18_customs_office_of_import": None,                                       # (c) Blocker — 수입 세관 코드 미수집
            "19_cn_code_of_goods": raw_data.get("hs_code"),                            # (a) seed채움
            "20_goods_description": raw_data.get("product_name") or raw_data.get("part_name") or None,  # (a) seed채움
            "21_country_of_origin": raw_data.get("item_origin") or raw_data.get("destination") or None,  # (a) seed채움
            "22_net_mass": raw_data.get("net_mass", 0.0),  # [3대 산식 변수] 수입 물품 순 중량
            "23_supplementary_units": float(raw_data.get("amperage_ah", 0.0)),
            "24_commercial_invoice_number": raw_data.get("invoice_number"),            # (a) seed채움
            "25_commercial_invoice_date": None,                                        # (c) Blocker — 인보이스 날짜 미수집
            "26_total_invoice_value": raw_data.get("unit_price", 0.0),
            "27_invoice_currency": "EUR",
            "28_terms_of_delivery": None,                                              # (b) N/A — Incoterms 미적용
            "29_nature_of_transaction": None,                                          # (b) N/A — 거래 성격 구분 불필요
            "30_mode_of_transport": None,                                              # (c) Blocker — 운송 수단 미수집
            "31_container_id": None,                                                   # (b) N/A — 컨테이너 단위 추적 없음
            "32_transport_document_number": None,                                      # (b) N/A — B/L 번호 미관리
            "33_economic_operator_name": raw_data.get("supplier_name_en"),             # (a) seed채움
            "34_national_customs_procedure_code": None,                                # (b) N/A — 국내 절차 코드 불필요
            "35_valuation_method": None                                                # (b) N/A — 관세 평가 방법 불필요
        },
        "section_3_installation_and_process": {
            "36_production_installation_id": str(uuid.uuid4()),
            "37_installation_name": raw_data.get("factory_name_en"),                   # (a) seed채움
            "38_country_of_installation": raw_data.get("country"),                     # (a) seed채움
            "39_installation_address": raw_data.get("address"),                        # (a) seed채움
            "40_geographical_coordinates": raw_data.get("location_wkt"),               # (a) seed채움
            "41_operator_name": raw_data.get("company_name_en"),                       # (a) seed채움
            "42_production_route": raw_data.get("manufacturing_process"),              # (a) seed채움
            "43_production_route_description": None,                                   # (b) N/A — 공정 서술 텍스트 없음
            "44_activity_level": raw_data.get("volume", 0.0),  # [1대 산식 분모]
            "45_system_boundaries_defined": None,                                      # (b) N/A — 시스템 경계 정의 불필요
            "46_direct_emissions_total": 0.0,
            "47_indirect_emissions_total": 0.0,
            "48_biomass_emissions": 0.0,
            "49_heating_steam_emissions": 0.0,
            "50_source_stream_data": None                                              # (b) N/A — 배출원 스트림 상세 없음
        },
        "section_4_embedded_emissions_scores": {
            "51_specific_direct_embedded_emissions": raw_data.get("carbon_intensity", 0.0),  # [3대 산식 결과]
            "52_specific_indirect_embedded_emissions": 0.0,  # [3대 산식 결과]
            "53_determination_methodology_direct": None,                               # (c) Blocker — 직접배출 산정 방법론 미정
            "54_determination_methodology_indirect": None,                             # (c) Blocker — 간접배출 산정 방법론 미정
            "55_electricity_consumption_factor": 0.0,
            "56_electricity_source": raw_data.get("energy_source"),                    # (a) seed채움
            "57_total_electricity_consumed": 0.0,
            "58_specific_embedded_emissions_total": 0.0,
            "59_total_embedded_emissions": raw_data.get("carbon_footprint", 0.0),  # [3대 산식 최종 스코어]
            "60_qualified_verifier_id": None,                                          # (b) N/A — 외부 검증인 없음(KIRA 자동 검증)
            "61_verifier_name": raw_data.get("auditor", "KIRA AI Verification Engine"),
            "62_verification_report_number": None,                                     # (c) Blocker — 검증 보고서 번호 미발급
            "63_verification_opinion_status": None,                                    # (c) Blocker — 검증 의견 미확정
            "64_accompanying_documents_identifier": raw_data.get("certification_no"),  # (a) seed채움
            "65_data_qualifying_flags": None                                           # (b) N/A — 플래그 체계 미도입
        },
        "section_5_precursors_and_carbon_price": {
            "66_precursor_type_indicator": "Complex",
            "67_precursor_cn_code": raw_data.get("precursor_hs_code"),                 # (a) seed채움
            "68_precursor_quantity_consumed": raw_data.get("precursor_quantity", 0.0),  # [2대 산식 변수]
            "69_precursor_specific_direct_emissions": 0.0,
            "70_precursor_specific_indirect_emissions": 0.0,
            "71_precursor_country_of_origin": raw_data.get("precursor_origin"),        # (a) seed채움
            "72_carbon_price_paid_indicator": "Y",
            "73_type_of_carbon_pricing_instrument": None,                              # (b) N/A — 탄소세 유형 구분 불필요
            "74_country_of_carbon_price": raw_data.get("tenant_country", "KR"),
            "75_amount_of_carbon_price_paid": 0.0,
            "76_currency_of_payment": "KRW",
            "77_quantity_of_covered_emissions": 0.0,
            "78_rebates_allocations_received": 0.0,
            "79_net_carbon_price_paid_score": 0.0,
            "80_carbon_price_supporting_docs": raw_data.get("origin_cert_url")        # (a) seed채움
        }
    }

    # CBAM 80필드 중 실제로 채워진(None이 아닌) 필드 수를 집계해서 수집률 배지용 메타로 얹어줘요.
    # (표시 문자열은 프론트 책임 — 백엔드는 숫자만 제공)
    all_cbam_values = [v for section in cbam_80_fields.values() for v in section.values()]
    data_completeness = {
        "filled": sum(1 for v in all_cbam_values if v is not None),
        "total": len(all_cbam_values)
    }

    return {
        "_data_completeness": data_completeness,
        "product_info": {
            "customer_id": str(raw_data.get("customer_id")) if raw_data.get("customer_id") else None,
            "customer_name": str(raw_data.get("customer_name", "Unknown")),
            "model_name": str(raw_data.get("model_name", "Unknown")),
            "amperage_ah": float(raw_data.get("amperage_ah", 0.0)),
        },
        "readiness_breakdown": readiness_breakdown,
        "scores": {
            "esg_compliance": esg_score,
            "traceability_coverage": traceability_score,
            "reliability": reliability_score
        },
        "annex_xiii_fields": cbam_80_fields
    }


# ── §6 프론트 계약 서비스 함수 ──────────────────────────────────────────────

async def list_dpp_records_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    destination: Optional[str] = None,
    approved_by: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[dict]:
    """6.1a 목록: products/suppliers 조인 + 필터 + tenant 격리."""
    filters = ["b.tenant_id = :tenant_id"]
    params: Dict[str, Any] = {"tenant_id": str(tenant_id), "limit": limit, "skip": skip}
    if destination:
        filters.append("b.destination = :destination")
        params["destination"] = destination
    if approved_by:
        filters.append("d.approved_by = :approved_by")
        params["approved_by"] = str(approved_by)
    if status:
        filters.append("d.status = :status")
        params["status"] = status
    if start_date:
        filters.append("d.issued_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("d.issued_at <= :end_date")
        params["end_date"] = end_date

    where = " AND ".join(filters)
    rows = (await db.execute(
        text(f"""
            SELECT
                d.dpp_id, d.product_id, p.product_code, p.model_name,
                s.company_name AS manufacturer,
                b.destination, d.approved_by, d.status, d.issued_at,
                d.carbon_footprint,
                d.recycled_content
            FROM dpp_records d
            JOIN batches b ON b.batch_id = d.batch_id
            JOIN products p ON p.product_id = d.product_id
            LEFT JOIN suppliers s ON s.supplier_id = p.manufacturer_id
            WHERE {where}
            ORDER BY d.issued_at DESC NULLS LAST
            LIMIT :limit OFFSET :skip
        """),
        params,
    )).mappings().fetchall()

    result = []
    for r in rows:
        row = dict(r)
        rc = row.get("recycled_content") or {}
        row["recycled_content"] = {"co": rc.get("Co"), "ni": rc.get("Ni"), "li": rc.get("Li")}
        result.append(row)
    return result


async def count_dpp_records_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    destination: Optional[str] = None,
    status: Optional[str] = None,
) -> int:
    """6.1a X-Total-Count."""
    filters = ["b.tenant_id = :tenant_id"]
    params: Dict[str, Any] = {"tenant_id": str(tenant_id)}
    if destination:
        filters.append("b.destination = :destination")
        params["destination"] = destination
    if status:
        filters.append("d.status = :status")
        params["status"] = status
    where = " AND ".join(filters)
    row = (await db.execute(
        text(f"""
            SELECT COUNT(d.dpp_id)
            FROM dpp_records d
            JOIN batches b ON b.batch_id = d.batch_id
            WHERE {where}
        """),
        params,
    )).scalar()
    return int(row or 0)


async def get_dpp_status_counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """6.2a: ready/hold/hitl/blocker/issued 카운트."""
    row = (await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE b.readiness_score >= 1.0 AND d.status IS NULL) AS ready_count,
                COUNT(*) FILTER (WHERE b.readiness_score < 1.0 AND d.status IS NULL)  AS hold_count,
                COUNT(*) FILTER (WHERE b.status = 'batch_hitl_wait')                  AS hitl_count,
                COUNT(*) FILTER (WHERE b.readiness_score < 1.0)                       AS blocker_count,
                COUNT(*) FILTER (WHERE d.status = 'dpp_issued')                       AS issued_count
            FROM batches b
            LEFT JOIN dpp_records d ON d.batch_id = b.batch_id
            WHERE b.tenant_id = :tenant_id
        """),
        {"tenant_id": str(tenant_id)},
    )).mappings().fetchone()
    return dict(row) if row else {}


async def get_held_products(db: AsyncSession, tenant_id: uuid.UUID) -> List[dict]:
    """6.2b / 6.3b: readiness < 1.0 제품 목록."""
    rows = (await db.execute(
        text("""
            SELECT
                p.product_id, p.product_name, b.destination,
                b.readiness_score AS readiness,
                b.received_at    AS last_updated_at,
                d.status
            FROM batches b
            JOIN products p ON p.product_id = b.product_id
            LEFT JOIN dpp_records d ON d.batch_id = b.batch_id
            WHERE b.tenant_id = :tenant_id
              AND (b.readiness_score IS NULL OR b.readiness_score < 1.0)
            ORDER BY b.readiness_score ASC NULLS FIRST
        """),
        {"tenant_id": str(tenant_id)},
    )).mappings().fetchall()
    return [dict(r) for r in rows]


async def get_dpp_blockers(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """6.2c: 도메인별 블로커 건수."""
    row = (await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE cr.verdict IN ('compliance_violation','compliance_reject')
                    AND r.regulation_code = 'IRA') AS feoc,
                COUNT(*) FILTER (WHERE cr.verdict IN ('compliance_violation','compliance_reject')
                    AND r.regulation_code != 'IRA') AS origin,
                COUNT(*) FILTER (WHERE b.status = 'batch_hitl_wait') AS hitl,
                COUNT(*) FILTER (WHERE sar.audit_status IN ('requested','assigned','in_progress')) AS audit
            FROM batches b
            LEFT JOIN compliance_results cr ON cr.batch_id = b.batch_id
            LEFT JOIN regulations r ON r.regulation_id = cr.regulation_id
            LEFT JOIN supplier_audit_records sar
                   ON sar.supplier_id IN (
                       SELECT target_supplier_id FROM data_request_log WHERE batch_id = b.batch_id LIMIT 1
                   )
            WHERE b.tenant_id = :tenant_id
        """),
        {"tenant_id": str(tenant_id)},
    )).mappings().fetchone()
    return dict(row) if row else {"feoc": 0, "origin": 0, "hitl": 0, "audit": 0}


async def get_carbon_trend(db: AsyncSession, tenant_id: uuid.UUID, days: int = 30) -> dict:
    """6.2d: 최근 N일 탄소발자국 일별 추이."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        text("""
            SELECT
                DATE(d.issued_at) AS label,
                AVG(d.carbon_footprint) AS value
            FROM dpp_records d
            JOIN batches b ON b.batch_id = d.batch_id
            WHERE b.tenant_id = :tenant_id
              AND d.issued_at >= :since
              AND d.carbon_footprint IS NOT NULL
            GROUP BY DATE(d.issued_at)
            ORDER BY label
        """),
        {"tenant_id": str(tenant_id), "since": since},
    )).mappings().fetchall()
    labels = [str(r["label"]) for r in rows]
    points = [float(r["value"] or 0) for r in rows]
    return {"labels": labels, "series": [{"name": "carbon_footprint", "points": points}]}


async def get_recycled_content_avg(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """6.2e: 재활용 함량 평균(Co/Ni/Li)."""
    row = (await db.execute(
        text("""
            SELECT
                AVG((d.recycled_content->>'Co')::float)   AS co_avg,
                AVG((d.recycled_content->>'Ni')::float)   AS ni_avg,
                AVG((d.recycled_content->>'Li')::float)   AS li_avg
            FROM dpp_records d
            JOIN batches b ON b.batch_id = d.batch_id
            WHERE b.tenant_id = :tenant_id
              AND d.recycled_content IS NOT NULL
        """),
        {"tenant_id": str(tenant_id)},
    )).mappings().fetchone()
    return dict(row) if row else {"co_avg": None, "ni_avg": None, "li_avg": None}


async def get_readiness_for_frontend(db: AsyncSession, product_id: uuid.UUID) -> dict:
    """6.3a: readiness를 프론트 계약 shape(checks[], blockers[])으로 반환."""
    from backend.domains.dpp.repository import get_readiness_metrics

    # 제품명 조회
    product_row = (await db.execute(
        text("SELECT product_name FROM products WHERE product_id = :pid"),
        {"pid": str(product_id)},
    )).mappings().fetchone()
    product_name = product_row["product_name"] if product_row else None

    breakdown = await get_readiness_metrics(db, product_id)
    passed_count = sum(1 for v in breakdown.values() if v)
    readiness = round(passed_count / len(breakdown), 2) if breakdown else 0.0

    label_map = {
        "all_tiers_completeness": ("required_data", "필수 데이터 완성도"),
        "no_violations": ("compliance", "컴플라이언스 위반 없음"),
        "origin_certs_valid": ("reliability", "원산지 증명서 유효"),
        "certifications_valid": ("reliability", "인증서 유효"),
        "training_completed": ("reliability", "교육 이수 완료"),
        "no_open_human_rights": ("due_diligence", "인권 이슈 없음"),
        "no_open_accidents": ("due_diligence", "산업재해 없음"),
        "trader_disclosure_ok": ("hitl", "트레이더 공개율 충족"),
    }

    checks = []
    blockers = []
    for key, passed in breakdown.items():
        check_key, label = label_map.get(key, (key, key))
        checks.append({"key": check_key, "label": label, "passed": passed})
        if not passed:
            blockers.append({"name": label, "related_doc": None, "due_date": None, "severity": "high"})

    return {
        "product_id": product_id,
        "product_name": product_name,
        "readiness": readiness,
        "checks": checks,
        "blockers": blockers,
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