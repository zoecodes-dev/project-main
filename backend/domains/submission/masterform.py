"""
domains/submission/masterform.py  (담당: 팀원 E 차윤)

마스터폼 섹션 4~6 write 함수.
  섹션 4: supplier_trader_details (지분·FEOC)
          → SupplierTraderDetail upsert + SupplierRiskProfile FEOC 필드 동기화
  섹션 5: supplier_human_rights_issues / supplier_audit_records /
          supplier_industrial_accidents / training_records (인권·실사·교육)
          → 섹션 단위 replace-all (기존 삭제 후 재입력)
  섹션 6: supplier_certifications (EoL·인증서)
          → replace-all

B(supplier/service.py)의 마스터폼 처리 함수가 단일 트랜잭션 내에서 호출한다.
커밋은 B의 service에서 단 한 번 수행한다. 이 파일에서 commit/rollback 하지 않는다.
"""
import uuid
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domains.supplier.models import (
    SupplierAuditRecord,
    SupplierCertification,
    SupplierHumanRightsIssue,
    SupplierIndustrialAccident,
    SupplierRiskProfile,
    SupplierTraderDetail,
    TrainingRecord,
    MasterFormCertifications,
    MasterFormOwnership,
    MasterFormSocial,
)


# ── 섹션 4: 지분·FEOC ─────────────────────────────────────────────────────
async def write_supplier_trader_details(
    db: AsyncSession,
    supplier_id: uuid.UUID,
    data: MasterFormOwnership,
) -> None:
    """
    supplier_trader_details: supplier당 1행 — 기존 삭제 후 재입력.
    FEOC 지분율이 입력된 경우 supplier_risk_profiles도 동기화한다.
    """
    # trader_details replace
    await db.execute(
        delete(SupplierTraderDetail).where(SupplierTraderDetail.supplier_id == supplier_id)
    )
    db.add(SupplierTraderDetail(
        supplier_id=supplier_id,
        trading_license=data.trading_license,
        broker_certification=data.broker_certification,
        disclosure_completeness=data.disclosure_completeness or 0.0,
    ))

    # FEOC 지분율 → risk_profile 동기화 (값이 있을 때만)
    if data.feoc_direct_ownership is not None or data.feoc_indirect_ownership is not None:
        result = await db.execute(
            select(SupplierRiskProfile).where(SupplierRiskProfile.supplier_id == supplier_id)
        )
        profile = result.scalar_one_or_none()
        if profile:
            if data.feoc_direct_ownership is not None:
                profile.feoc_direct_ownership = data.feoc_direct_ownership
            if data.feoc_indirect_ownership is not None:
                profile.feoc_indirect_ownership = data.feoc_indirect_ownership
        else:
            db.add(SupplierRiskProfile(
                supplier_id=supplier_id,
                feoc_direct_ownership=data.feoc_direct_ownership,
                feoc_indirect_ownership=data.feoc_indirect_ownership,
                overall_risk_score=0,
                risk_level="low",
                feoc_status="unknown",
                is_high_risk_flag=False,
            ))


# ── 섹션 5: 인권·실사·교육 ────────────────────────────────────────────────
async def write_supplier_social(
    db: AsyncSession,
    supplier_id: uuid.UUID,
    factory_ids: List[uuid.UUID],
    data: MasterFormSocial,
) -> None:
    """
    4개 테이블 replace-all.
    MasterFormTrainingRecord / MasterFormAuditRecord 등의 factory_index는
    factories 리스트 내 순서 인덱스이며, factory_ids[idx]로 FK를 연결한다.
    """

    def _fid(idx: Optional[int]) -> Optional[uuid.UUID]:
        if idx is None or not factory_ids:
            return None
        return factory_ids[idx] if 0 <= idx < len(factory_ids) else None

    # 인권 이슈
    await db.execute(
        delete(SupplierHumanRightsIssue).where(SupplierHumanRightsIssue.supplier_id == supplier_id)
    )
    for item in data.human_rights_issues:
        db.add(SupplierHumanRightsIssue(
            supplier_id=supplier_id,
            issue_type=item.issue_type,
            severity=item.severity,
            description=item.description,
            status=item.status,
            source=item.source,
        ))

    # 실사 기록
    await db.execute(
        delete(SupplierAuditRecord).where(SupplierAuditRecord.supplier_id == supplier_id)
    )
    for rec in data.audit_records:
        db.add(SupplierAuditRecord(
            supplier_id=supplier_id,
            audit_date=rec.audit_date,
            audit_type=rec.audit_type,
            auditor=rec.auditor,
            audit_scope=rec.audit_scope,
            result=rec.result,
            next_audit_due=rec.next_audit_due,
            report_url=rec.report_url,
        ))

    # 산업재해
    await db.execute(
        delete(SupplierIndustrialAccident).where(SupplierIndustrialAccident.supplier_id == supplier_id)
    )
    for acc in data.industrial_accidents:
        db.add(SupplierIndustrialAccident(
            supplier_id=supplier_id,
            accident_date=acc.accident_date,
            accident_type=acc.accident_type,
            description=acc.description,
            casualties=acc.casualties,
            ltifr=acc.ltifr,
            status=acc.status,
            corrective_action=acc.corrective_action,
        ))

    # 교육 이수 기록
    await db.execute(
        delete(TrainingRecord).where(TrainingRecord.supplier_id == supplier_id)
    )
    for tr in data.training_records:
        db.add(TrainingRecord(
            supplier_id=supplier_id,
            factory_id=_fid(tr.factory_index),
            material_id=tr.material_id,
            trainee_count=tr.trainee_count,
            total_eligible=tr.total_eligible,
            completion_rate=tr.completion_rate or 0.0,
            completed_at=tr.completed_at,
            due_date=tr.due_date,
            status=tr.status,
            instructor=tr.instructor,
            notes=tr.notes,
        ))


# ── 섹션 6: EoL·인증서 ────────────────────────────────────────────────────
async def write_supplier_certifications(
    db: AsyncSession,
    supplier_id: uuid.UUID,
    data: MasterFormCertifications,
) -> None:
    """
    supplier_certifications replace-all.
    """
    await db.execute(
        delete(SupplierCertification).where(SupplierCertification.supplier_id == supplier_id)
    )
    for cert in data.certifications:
        db.add(SupplierCertification(
            supplier_id=supplier_id,
            certification_type=cert.certification_type,
            certification_no=cert.certification_no,
            issued_at=cert.issued_at,
            expires_at=cert.expires_at,
            issuing_body=cert.issuing_body,
            document_url=cert.document_url,
        ))
