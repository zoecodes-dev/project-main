import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.trace import trace_tool
from backend.domains.dpp.repository import get_dpp_record, get_score_raw_data
from backend.domains.dpp.models import DppDeliveryHistory


@trace_tool("generate_delivery_form")
async def generate_delivery_form(db: AsyncSession, dpp_id: uuid.UUID) -> dict:
    """
    [대외 전송 양식 생성]
    DPP 발급 데이터를 기반으로 고객사에 전송할 이메일/메시지 양식을 생성합니다.
    (직접 SMTP 발송이 아닌, 사람이 확인 후 발송할 수 있도록 프리뷰 텍스트를 제공합니다.)
    """
    # 1. DPP 레코드 확인
    dpp_record = await get_dpp_record(db, dpp_id)
    if not dpp_record:
        raise ValueError("해당 DPP를 찾을 수 없습니다.")

    # 2. 고객사 수신처 자동 채움 (도메인 분리 원칙: raw SQL 헬퍼인 get_score_raw_data 재사용)
    raw_data = await get_score_raw_data(db, dpp_record.batch_id)
    customer_name = raw_data.get("customer_name") or "고객사"
    customer_id = raw_data.get("customer_id")
    
    # 3. 양식 데이터 채우기
    dpp_id_str = str(dpp_record.dpp_id)
    qr_url = dpp_record.qr_code_url or f"https://dpp.kira.compliance/verify/{dpp_record.batch_id}"
    carbon_footprint = dpp_record.carbon_footprint or 0.0
    
    now_utc = datetime.now(timezone.utc)
    formatted_date = now_utc.strftime('%Y-%m-%d')
    
    subject = f"[KIRA] 배터리 제품 탄소발자국 및 공급망 검증 완료 안내 (DPP ID: {dpp_id_str})"
    
    body_text = f"""안녕하세요, {customer_name} 담당자님.

KIRA Compliance Intelligence Platform을 통해 귀사 배터리 제품의 공급망 및 탄소발자국(DPP) 검증이 성공적으로 완료되었습니다.

[DPP 발급 요약]
- DPP ID: {dpp_id_str}
- 검증 완료일: {formatted_date}
- 탄소발자국 총량: {carbon_footprint} kgCO2eq
- 검증 결과: 적합 (Pass)

아래 URL(또는 QR 코드)을 통해 귀사 제품의 상세 디지털 여권(DPP) 리포트를 확인하실 수 있습니다.
- DPP 조회 URL: {qr_url}

본 결과는 EU Battery Regulation 및 주요 규제 대응을 위한 공식 증빙 자료로 활용하실 수 있습니다.
추가 문의 사항이 있으시면 본 메일로 회신 부탁드립니다.

감사합니다.
KIRA Compliance Team 드림
"""

    return {
        "dpp_id": dpp_id_str,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "subject": subject,
        "body_text": body_text,
        "qr_code_url": qr_url,
        "generated_at": now_utc.isoformat()
    }


@trace_tool("record_delivery_history")
async def record_delivery_history(
    db: AsyncSession, 
    dpp_id: uuid.UUID, 
    recipient_email: str, 
    subject: str, 
    body_text: str, 
    user_id: uuid.UUID
) -> dict:
    """
    [전송 이력 기록] 사람이 발송을 완료한 후, 그 이력을 DB에 남깁니다.
    """
    dpp_record = await get_dpp_record(db, dpp_id)
    if not dpp_record:
        raise ValueError("해당 DPP를 찾을 수 없습니다.")
        
    raw_data = await get_score_raw_data(db, dpp_record.batch_id)
    customer_id = raw_data.get("customer_id")
        
    history = DppDeliveryHistory(
        dpp_id=dpp_id,
        customer_id=uuid.UUID(customer_id) if customer_id else None,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
        sent_by=user_id
    )
    db.add(history)
    await db.flush()
    
    return {
        "delivery_id": str(history.delivery_id),
        "status": "recorded",
        "sent_at": history.sent_at.isoformat() if history.sent_at else datetime.now(timezone.utc).isoformat()
    }