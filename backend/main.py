"""
main.py — KIRA Compliance Intelligence Platform 진입점.

startup 시 PostGIS/pgvector 확장을 검증하고, 각 도메인 라우터를 등록한다.
실행: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from backend.infrastructure.database import verify_extensions
from backend.domains.supplychain.router import router as supplychain_router
from backend.domains.submission.router import router as submission_router
from backend.domains.verification.router import router as verification_router

from backend.domains.product.router import router as product_router
from backend.domains.supplier.router import router as supplier_router
from backend.domains.audit.router import actions_router, router as audit_router
from backend.domains.risk.router import router as risk_router
from backend.domains.dpp.router import router as dpp_router
from backend.hitl.router import router as hitl_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: 필수 확장 검증
    await verify_extensions()
    yield
    # shutdown: (필요 시 정리)


app = FastAPI(
    title="KIRA Compliance Intelligence Platform",
    description="N차 공급망 추적 및 Geo Audit 기반 DPP 발행 백엔드",
    lifespan=lifespan,
)

# 도메인 라우터 등록 (도메인 추가 시 여기에 include)
app.include_router(supplychain_router)
app.include_router(submission_router)
app.include_router(verification_router)

app.include_router(supplier_router)
app.include_router(product_router)
app.include_router(audit_router)
app.include_router(actions_router)
app.include_router(risk_router)
app.include_router(dpp_router)
app.include_router(hitl_router)

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
