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
