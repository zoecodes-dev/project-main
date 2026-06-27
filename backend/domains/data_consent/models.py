"""
domains/data_consent/models.py

제3자 정보제공 동의서 = 데이터 계약(Data Contract) DTO.
Catena-X 데이터 주권 모델 정렬: 데이터 자산(data_scope) + 목적(purpose=ODRL) +
재공유 정책(third_party_sharing/allowed_recipients) + 기간/철회 + 협상 상태(status) +
회신 양식 데이터(form_data) + 증빙/무결성(document_file_id/agreement_hash).
"""
import uuid
from datetime import date, datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel


# 동의서 발송(원청→협력사) — 데이터 계약 '오퍼' 생성.
class ConsentCreateBody(BaseModel):
    supplier_id: uuid.UUID
    data_scope: List[str]                       # ["company","contacts","factories","carbon_epd","origin","sub_suppliers"]
    purpose: str                                # EU_BATTERY / SUPPLY_CHAIN_DD / CSDDD / CONFLICT_MINERALS
    third_party_sharing: bool = False
    allowed_recipients: Optional[List[str]] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    revocable: bool = True
    form_version: Optional[str] = None


# 회신/서명/철회 — 데이터 계약 협상 상태 전이 + 회신 양식 데이터 영속.
class ConsentUpdateBody(BaseModel):
    status: str                                 # returned / agreed / rejected / revoked / expired
    signer_name: Optional[str] = None
    signer_title: Optional[str] = None
    signer_email: Optional[str] = None
    signature_method: Optional[str] = None      # email_form / e_sign / wet_signature
    form_data: Optional[Dict[str, Any]] = None  # 회신받은 구조화 양식 데이터
    document_file_id: Optional[uuid.UUID] = None
    agreement_hash: Optional[str] = None


class ConsentResponse(BaseModel):
    consent_id: uuid.UUID
    supplier_id: uuid.UUID
    tenant_id: Optional[uuid.UUID] = None
    data_scope: List[str] = []
    purpose: str
    third_party_sharing: bool
    allowed_recipients: Optional[List[str]] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    revocable: bool
    status: str
    requested_at: Optional[datetime] = None
    returned_at: Optional[datetime] = None
    agreed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    signer_name: Optional[str] = None
    signer_title: Optional[str] = None
    signer_email: Optional[str] = None
    signature_method: Optional[str] = None
    form_version: Optional[str] = None
    form_data: Optional[Dict[str, Any]] = None
    document_file_id: Optional[uuid.UUID] = None
    agreement_hash: Optional[str] = None
    created_at: Optional[datetime] = None
