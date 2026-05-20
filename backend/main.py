from fastapi import FastAPI

app = FastAPI(
    title="KIRA Compliance Intelligence Platform API",
    description="Layer 1 데이터 백본 및 Layer 2 KIRA 에이전트를 위한 통합 백엔드 인프라",
    version="1.0.0"
)

@app.get("/health", tags=["Infrastructure"])
async def health_check():
    return {
        "status": "healthy",
        "environment": "development",
        "version": "1.0.0"
    }