"""
main.py — KIRA Compliance Intelligence Platform 진입점.

startup 시 PostGIS/pgvector 확장을 검증하고, 각 도메인 라우터를 등록한다.
실행: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from backend.infrastructure.database import verify_extensions
from backend.agents.graph import resume_graph, setup_graph, teardown_graph
from backend.infrastructure.event_bus import start_event_listener, stop_event_listener, subscribe
from backend.domains.supplychain.router import router as supplychain_router
from backend.domains.submission.router import router as submission_router
from backend.domains.verification.router import router as verification_router

from backend.domains.users.router import router as users_router
from backend.domains.report.router import router as report_router
from backend.domains.product.router import router as product_router
from backend.domains.supplier.router import router as supplier_router
from backend.domains.audit.router import actions_router, router as audit_router
from backend.domains.risk.router import router as risk_router
from backend.domains.dpp.router import router as dpp_router
from backend.hitl.router import router as hitl_router
from backend.domains.batches.router import batches_router, dashboard_router
from backend.domains.acl.router import router as acl_router

async def _on_hitl_resolved(payload: dict) -> None:
    batch_id = payload.get("batch_id", "")
    resolution = payload.get("resolution", "")
    if batch_id and resolution:
        await resume_graph(batch_id, resolution)


async def _register_subscriptions() -> None:
    """
    이벤트 구독 슬롯 (담당: 팀원 B — 인프라/골격).

    도메인 간 이벤트 핸들러를 한곳에서 등록하는 단일 지점이다. 각 담당은 자기
    이벤트 핸들러를 여기에 한 줄로 배선한다(handler는 자기 도메인/모듈에 둔다).
    구독은 start_event_listener()가 띄운 LISTEN 루프가 디스패치한다.

    배선 규칙:
      - handler 본체는 절대 여기에 두지 않는다(여기는 '슬롯'일 뿐). 자기 모듈에 두고
        import해서 subscribe()로 등록만 한다.
      - 같은 event_name에 여러 핸들러를 붙일 수 있다(event_bus는 다중 핸들러 지원).
    """
    # HITL 재개 (A) — interrupt 해소 시 그래프 resume
    await subscribe("hitl.resolved", _on_hitl_resolved)

    # ── A1: 배치 생성 + graph 트리거 ─────────────────────────────────
    from backend.handlers.batch_trigger import on_submission_approved
    await subscribe("SubmissionApproved", on_submission_approved)
    await subscribe("SubmissionCompleted", on_submission_approved)

    # ── 그 외 도메인 핸들러 슬롯 (D: discovered_via 기록 / E: 알림 등) ──
    # 예) await subscribe("SupplierInvited", supplychain_record_discovered_via)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: 필수 확장 검증 + checkpoint DB 초기화 + 이벤트 구독 등록 + LISTEN 루프 기동
    await verify_extensions()
    await setup_graph()
    await _register_subscriptions()
    await start_event_listener()
    yield
    # shutdown: LISTEN 루프 정리 + checkpoint 풀 해제
    await stop_event_listener()
    await teardown_graph()


app = FastAPI(
    title="KIRA Compliance Intelligence Platform",
    description="N차 공급망 추적 및 Geo Audit 기반 DPP 발행 백엔드",
    lifespan=lifespan,
)

# 도메인 라우터 등록 (도메인 추가 시 여기에 include)
app.include_router(supplychain_router)
app.include_router(submission_router)
app.include_router(verification_router)

app.include_router(users_router)
app.include_router(report_router)
app.include_router(supplier_router)
app.include_router(product_router)
app.include_router(audit_router)
app.include_router(actions_router)
app.include_router(risk_router)
app.include_router(dpp_router)
app.include_router(hitl_router)
app.include_router(batches_router)
app.include_router(dashboard_router)
app.include_router(acl_router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "KIRA Backend is running"}

WELCOME_MSG = r"""
└[o_o]┘  Welcome Home !
   [-]    FastAPI is running...
"""

@app.get("/", response_class=PlainTextResponse)
def read_root():
    return WELCOME_MSG
