import uuid
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from backend.infrastructure.database import get_db
from backend.domains.verification.service import verify_feoc_rule

router = APIRouter(prefix="/verification", tags=["Verification"])

class FEOCDummyRequest(BaseModel):
    """
    [DTO] FEOC 룰 실동작 테스트를 위한 더미 데이터 요청 스키마
    """
    batch_id: uuid.UUID
    supplier_id: uuid.UUID
    direct_ownership: float
    indirect_ownership: float = 0.0

@router.post("/feoc-test", status_code=status.HTTP_200_OK)
async def trigger_dummy_feoc_rule(req: FEOCDummyRequest, db: AsyncSession = Depends(get_db)):
    """
    [API] POST /verification/feoc-test
    더미 지분율 데이터를 주입하여 FEOC 25% 초과 규제 룰을 테스트합니다.
    - 25% 초과 시: VerificationFailed 발행 및 Queue 적재
    - 25% 이하 시: VerificationCompleted 발행
    """
    try:
        is_passed = await verify_feoc_rule(
            db=db,
            batch_id=req.batch_id,
            supplier_id=req.supplier_id,
            direct_ownership=req.direct_ownership,
            indirect_ownership=req.indirect_ownership
        )
        
        # [추가] @trace_tool 데코레이터가 남긴 '감사 기록(Audit Trail)'을
        # 데이터베이스에 영구적으로 확정(저장)하기 위해 커밋을 호출합니다.
        await db.commit()
    except Exception as e:
        await db.rollback()
        # [개선] 비즈니스 로직 중복을 피하고, 에러 상황을 명확히 알립니다.
        # 더미 UUID 사용으로 인한 DB 오류 시, 규칙 검증 로직 자체가 실패했음을 알리는
        # 명확한 메시지를 반환하여 테스트 실패 원인을 쉽게 파악하도록 합니다.
        return {
            "status": "bypassed_due_to_error",
            "message": f"서비스 로직 실행 중 DB 오류가 발생하여 테스트가 중단되었습니다. (오류: {e})",
            "note": "더미 UUID를 사용한 경우 예상된 동작일 수 있습니다. FEOC 규칙 자체는 검증되지 않았습니다."
        }
    
    if is_passed:
        return {"status": "passed", "message": "FEOC 검증 통과 (우려국 지분 25% 미만)"}
    else:
        # [수정] 중복 enqueue 호출 제거.
        # verify_feoc_rule 서비스 함수가 내부적으로 위반 시 Queue 적재 및 이벤트 발행을
        # 모두 처리하므로, 라우터에서는 결과만 반환합니다.
        return {"status": "compliance_violation", "message": "FEOC 규제 위반 (25% 이상) - 후속 작업이 비동기 처리됩니다."}