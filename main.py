from fastapi import FastAPI
from router import router as supplychain_router

app = FastAPI(
    title="KIRA Compliance Intelligence Platform",
    description="N차 공급망 추적 및 Geo Audit API"
)

app.include_router(supplychain_router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "KIRA Backend is running"}