"""
domains/data_consent/repository.py

data_provision_consents 테이블 DB 접근. text() SQL(JSONB 캐스팅). 커밋은 service.
"""
import json
import uuid
from typing import Optional, List, Dict, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_COLS = """
    consent_id, supplier_id, tenant_id, data_scope, purpose, third_party_sharing,
    allowed_recipients, valid_from, valid_to, revocable, status,
    requested_at, returned_at, agreed_at, revoked_at,
    signer_name, signer_title, signer_email, signature_method,
    form_version, form_data, document_file_id, agreement_hash, created_at
"""


async def create(db: AsyncSession, *, tenant_id, requested_by, body) -> Dict[str, Any]:
    q = text(f"""
        INSERT INTO data_provision_consents
          (supplier_id, tenant_id, data_scope, purpose, third_party_sharing, allowed_recipients,
           valid_from, valid_to, revocable, status, requested_at, form_version, requested_by)
        VALUES
          (:supplier_id, :tenant_id, CAST(:data_scope AS JSONB), :purpose, :third_party_sharing,
           CAST(:allowed_recipients AS JSONB), :valid_from, :valid_to, :revocable,
           'requested', now(), :form_version, :requested_by)
        RETURNING {_COLS};
    """)
    res = await db.execute(q, {
        "supplier_id": str(body.supplier_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "data_scope": json.dumps(body.data_scope),
        "purpose": body.purpose,
        "third_party_sharing": body.third_party_sharing,
        "allowed_recipients": json.dumps(body.allowed_recipients) if body.allowed_recipients is not None else None,
        "valid_from": body.valid_from,
        "valid_to": body.valid_to,
        "revocable": body.revocable,
        "form_version": body.form_version,
        "requested_by": str(requested_by) if requested_by else None,
    })
    await db.flush()
    return dict(res.mappings().first())


async def list_by_supplier(db: AsyncSession, supplier_id: uuid.UUID, tenant_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
    where = "WHERE supplier_id = :sid"
    params: Dict[str, Any] = {"sid": str(supplier_id)}
    if tenant_id is not None:
        where += " AND (tenant_id = :tid OR tenant_id IS NULL)"
        params["tid"] = str(tenant_id)
    q = text(f"SELECT {_COLS} FROM data_provision_consents {where} ORDER BY created_at DESC")
    res = await db.execute(q, params)
    return [dict(r) for r in res.mappings().all()]


async def update_status(db: AsyncSession, consent_id: uuid.UUID, body) -> Optional[Dict[str, Any]]:
    # 상태 전이 시각을 status에 맞춰 자동 기록(returned/agreed/revoked).
    ts_col = {"returned": "returned_at", "agreed": "agreed_at", "revoked": "revoked_at"}.get(body.status)
    ts_set = f", {ts_col} = now()" if ts_col else ""
    q = text(f"""
        UPDATE data_provision_consents SET
            status = :status,
            signer_name = COALESCE(:signer_name, signer_name),
            signer_title = COALESCE(:signer_title, signer_title),
            signer_email = COALESCE(:signer_email, signer_email),
            signature_method = COALESCE(:signature_method, signature_method),
            form_data = COALESCE(CAST(:form_data AS JSONB), form_data),
            document_file_id = COALESCE(:document_file_id, document_file_id),
            agreement_hash = COALESCE(:agreement_hash, agreement_hash),
            updated_at = now(){ts_set}
        WHERE consent_id = :cid
        RETURNING {_COLS};
    """)
    res = await db.execute(q, {
        "cid": str(consent_id),
        "status": body.status,
        "signer_name": body.signer_name,
        "signer_title": body.signer_title,
        "signer_email": body.signer_email,
        "signature_method": body.signature_method,
        "form_data": json.dumps(body.form_data) if body.form_data is not None else None,
        "document_file_id": str(body.document_file_id) if body.document_file_id else None,
        "agreement_hash": body.agreement_hash,
    })
    await db.flush()
    row = res.mappings().first()
    return dict(row) if row else None
