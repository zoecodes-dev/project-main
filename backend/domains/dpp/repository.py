import uuid
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.dpp.models import DppRecord
from backend.infrastructure.trace import trace_tool

from sqlalchemy import text


@trace_tool("get_dpp_record")
async def get_dpp_record(db: AsyncSession, dpp_id: uuid.UUID) -> DppRecord | None:
    """단건 DPP 기록을 조회합니다."""
    stmt = select(DppRecord).where(DppRecord.dpp_id == dpp_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@trace_tool("get_readiness_metrics")
async def get_readiness_metrics(db: AsyncSession, product_id: uuid.UUID) -> Dict[str, bool]:
    """
    제품의 공급망을 역추적하여 Readiness 8대 체크리스트 충족 여부를 한 번에 진단해요.
    """
    # 1. 대상 제품의 활성 BOM에 속한 모든 공급망 식별자 추출
    supplier_query = text("""
        SELECT DISTINCT scm.child_supplier_id
        FROM bom_versions bv
        JOIN supply_chain_map scm ON bv.bom_version_id = scm.bom_version_id
        WHERE bv.product_id = :product_id AND bv.status = 'active'
    """)
    result = await db.execute(supplier_query, {"product_id": product_id})
    supplier_ids = [row[0] for row in result.fetchall()]

    if not supplier_ids:
        # 공급망이 구성되지 않았다면 기본적으로 모든 항목 미충족
        return {
            "all_tiers_completeness": False,
            "no_violations": False,
            "origin_certs_valid": False,
            "certifications_valid": False,
            "training_completed": False,
            "no_open_human_rights": False,
            "no_open_accidents": False,
            "trader_disclosure_ok": False,
        }

    params = {"supplier_ids": supplier_ids}

    # 2. 8개 항목 검사 (SQLAlchemy 비동기 세션 동시성 충돌 방지를 위해 순차 실행)
    c_comp = (await db.execute(text("SELECT COUNT(*) FROM data_completeness_status WHERE entity_type = 'supplier' AND entity_id = ANY(:supplier_ids) AND completion_rate < 80"), params)).scalar()
    
    c_viol = (await db.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM data_request_log WHERE target_supplier_id = ANY(:supplier_ids) AND submission_status = 'rejected'
            UNION ALL
            SELECT 1 FROM compliance_results WHERE supplier_id = ANY(:supplier_ids) AND verdict = 'violation'
        ) t
    """), params)).scalar()

    c_orig = (await db.execute(text("SELECT COUNT(*) FROM origin_certificates WHERE supplier_id = ANY(:supplier_ids) AND status = 'expired'"), params)).scalar()
    c_cert = (await db.execute(text("SELECT COUNT(*) FROM supplier_certifications WHERE supplier_id = ANY(:supplier_ids) AND expires_at < CURRENT_DATE"), params)).scalar()
    c_train = (await db.execute(text("SELECT COUNT(*) FROM training_records WHERE supplier_id = ANY(:supplier_ids) AND status = 'overdue'"), params)).scalar()
    c_hr = (await db.execute(text("SELECT COUNT(*) FROM supplier_human_rights_issues WHERE supplier_id = ANY(:supplier_ids) AND status = 'open'"), params)).scalar()
    c_acc = (await db.execute(text("SELECT COUNT(*) FROM supplier_industrial_accidents WHERE supplier_id = ANY(:supplier_ids) AND status = 'investigating'"), params)).scalar()
    c_trader = (await db.execute(text("SELECT COUNT(*) FROM trader_disclosure_obligation WHERE trader_supplier_id = ANY(:supplier_ids) AND disclosure_completeness < 75"), params)).scalar()

    return {
        "all_tiers_completeness": c_comp == 0,
        "no_violations": c_viol == 0,
        "origin_certs_valid": c_orig == 0,
        "certifications_valid": c_cert == 0,
        "training_completed": c_train == 0,
        "no_open_human_rights": c_hr == 0,
        "no_open_accidents": c_acc == 0,
        "trader_disclosure_ok": c_trader == 0,
    }


@trace_tool("get_score_raw_data")
async def get_score_raw_data(db: AsyncSession, batch_id: uuid.UUID) -> Dict[str, Any]:
    """
    [도메인 격리 준수]
    3대 점수 산식(ESG/Traceability/Reliability)에 필요한 데이터를
    다른 도메인 모델 침범 없이 Raw SQL로 한 번에 추출합니다.
    """
    # 1. Batch 기본 정보 및 CBAM 80필드에 필요한 연동 데이터 추출
    batch_query = text("""
        SELECT 
            b.bom_version_id, 
            b.destination,
            t.business_reg_no,
            t.company_name AS tenant_company_name,
            'KR' AS tenant_country,
            pr.product_name,
            bi.origin_country AS item_origin,
            p.hs_code,
            p.part_name,
            p.unit_price,
            s.company_name_en AS supplier_name_en,
            sf.factory_name_en,
            sf.country,
            sf.address,
            ST_AsText(sf.location) AS location_wkt,
            smd.manufacturing_process,
            smd.energy_source,
            smd.carbon_intensity,
            sr.volume,
            dpp.carbon_footprint,
            sc.name AS contact_name,
            sc.email AS contact_email,
            scm.invoice_number,
            scert.certification_no
        FROM batches b
        JOIN tenants t ON b.tenant_id = t.tenant_id
        JOIN bom_versions bv ON b.bom_version_id = bv.bom_version_id
        JOIN products pr ON b.product_id = pr.product_id
        LEFT JOIN bom_items bi ON bv.bom_version_id = bi.bom_version_id
        -- 최상위 납품(1차 협력사) 정보 조인을 위한 supply_chain_map (hop_level=1)
        LEFT JOIN supply_chain_map scm ON scm.bom_version_id = b.bom_version_id AND scm.hop_level = 1
        LEFT JOIN parts p ON scm.part_id = p.part_id
        LEFT JOIN suppliers s ON scm.child_supplier_id = s.supplier_id
        LEFT JOIN supplier_factories sf ON s.supplier_id = sf.supplier_id AND sf.is_active = TRUE
        LEFT JOIN supplier_manufacturer_details smd ON s.supplier_id = smd.supplier_id
        LEFT JOIN supply_ratio sr ON scm.map_id = sr.map_id
        LEFT JOIN dpp_records dpp ON b.batch_id = dpp.batch_id
        LEFT JOIN supplier_contacts sc ON s.supplier_id = sc.supplier_id AND sc.is_primary = TRUE
        LEFT JOIN supplier_certifications scert ON s.supplier_id = scert.supplier_id
        WHERE b.batch_id = :batch_id
        LIMIT 1
    """)
    batch_row = (await db.execute(batch_query, {"batch_id": batch_id})).mappings().fetchone()
    if not batch_row:
        raise ValueError("배치 정보를 찾을 수 없습니다.")
    bom_version_id = batch_row["bom_version_id"]
    destination = batch_row["destination"]

    # 2. ESG Compliance (verdict 집계)
    comp_query = text("""
        SELECT verdict, COUNT(*) as cnt
        FROM compliance_results
        WHERE batch_id = :batch_id
        GROUP BY verdict
    """)
    comp_rows = (await db.execute(comp_query, {"batch_id": batch_id})).fetchall()
    compliance_counts = {row[0]: row[1] for row in comp_rows}

    # 3. Traceability Coverage (노드 승인 및 연결 확정 비율)
    # TODO: supply_chain_map에 link_status 컬럼이 B migration으로 추가된 후 주석 해제
    trace_query = text("""
        SELECT
            COUNT(*) as total_nodes,
            SUM(
                CASE
                    WHEN v.submission_status = 'submission_approved'
                    /* AND scm.link_status = 'supplychain_confirmed' */
                    THEN 1 ELSE 0
                END
            ) as approved_nodes
        FROM v_supply_chain_node_status v
        JOIN supply_chain_map scm ON v.map_id = scm.map_id
        WHERE scm.bom_version_id = :bom_version_id
    """)
    trace_row = (await db.execute(trace_query, {"bom_version_id": bom_version_id})).fetchone()
    traceability = {"total": trace_row[0] or 0, "approved": trace_row[1] or 0}

    # 4. Reliability Score 요소 (협력사 완성도, SLA 위반 건수, 리스크)
    rel_query = text("""
        SELECT
            s.supplier_id,
            s.completeness_score,
            s.risk_level,
            (
                SELECT COUNT(*) FROM data_request_log drl
                WHERE drl.target_supplier_id = s.supplier_id
                  AND drl.response_status IN ('response_overdue', 'response_escalated')
            ) as overdue_count
        FROM suppliers s
        JOIN supply_chain_map scm ON scm.child_supplier_id = s.supplier_id
        WHERE scm.bom_version_id = :bom_version_id
    """)
    rel_rows = (await db.execute(rel_query, {"bom_version_id": bom_version_id})).fetchall()
    suppliers_data = [
        {"completeness": r[1] or 0, "risk_level": r[2] or "unknown", "overdue_count": r[3] or 0}
        for r in rel_rows
    ]
        
    return {
        "destination": destination,
        "compliance": compliance_counts,
        "traceability": traceability,
        "suppliers": suppliers_data,
        
        # CBAM 연동용 메타데이터 (generator.py로 전달)
        "business_reg_no": batch_row.get("business_reg_no"),
        "tenant_company_name": batch_row.get("tenant_company_name"),
        "tenant_country": batch_row.get("tenant_country"),
        "contact_name": batch_row.get("contact_name"),
        "contact_email": batch_row.get("contact_email"),
        "hs_code": batch_row.get("hs_code"),
        "part_name": batch_row.get("part_name"),
        "product_name": batch_row.get("product_name"),
        "item_origin": batch_row.get("item_origin"),
        "certification_no": batch_row.get("certification_no"),
        "unit_price": float(batch_row["unit_price"]) if batch_row.get("unit_price") is not None else 0.0,
        "supplier_name_en": batch_row.get("supplier_name_en"),
        "factory_name_en": batch_row.get("factory_name_en"),
        "country": batch_row.get("country"),
        "address": batch_row.get("address"),
        "location_wkt": batch_row.get("location_wkt"),
        "manufacturing_process": batch_row.get("manufacturing_process"),
        "volume": float(batch_row["volume"]) if batch_row.get("volume") is not None else 0.0,
        "carbon_intensity": float(batch_row["carbon_intensity"]) if batch_row.get("carbon_intensity") is not None else 0.0,
        "energy_source": batch_row.get("energy_source"),
        "carbon_footprint": float(batch_row["carbon_footprint"]) if batch_row.get("carbon_footprint") is not None else 0.0,
        "invoice_number": batch_row.get("invoice_number")
    }