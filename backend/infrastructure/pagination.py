"""
infrastructure/pagination.py  (담당: 팀원 B / 공통)

목록 응답 페이지네이션 공통 헬퍼 (§0.6).
- 응답 본문은 envelope 없는 bare array 로 유지한다(envelope 금지).
- 전체 건수는 **X-Total-Count 응답 헤더**로 전달 → 프론트가 ceil(total/size)로 페이지 수 계산.

사용 패턴(전 도메인 목록 엔드포인트 공통):
    from fastapi import Response
    from backend.infrastructure.pagination import set_total_count

    @router.get("", response_model=List[XBrief])
    async def list_x(response: Response, ..., db=Depends(get_db)):
        items = await service.list_x(db, ..., page, size, tenant_id)
        total = await service.count_x(db, ..., tenant_id)   # 필터 동일, 페이지 무관 전체 건수
        set_total_count(response, total)
        return items

주의: next.config.js rewrite 가 헤더를 그대로 통과시킨다(현재 단순 rewrite). 향후
CORS 직결(브라우저가 EC2 직접 호출) 시에는 백엔드가
`Access-Control-Expose-Headers: X-Total-Count` 를 설정해야 프론트가 헤더를 읽는다.
"""
from starlette.responses import Response

TOTAL_COUNT_HEADER = "X-Total-Count"


def set_total_count(response: Response, total: int) -> None:
    """목록 응답에 전체 건수 헤더를 단다(필터 적용 후 전체 건수, 현재 페이지 건수가 아님)."""
    response.headers[TOTAL_COUNT_HEADER] = str(total)
