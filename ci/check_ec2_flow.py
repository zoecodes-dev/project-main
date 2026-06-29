"""
ci/check_ec2_flow.py — EC2 컨테이너 내부 데이터흐름 / Bedrock 점검 (SSM 경유 실행)

EC2 Role(KIRA-EC2-Bedrock-Role)이 잡히는 app 컨테이너 안에서 실행해, 풀 E2E의 전제
(AI 자격 + 데이터 적재 + RAG 준비)를 한 번에 확인한다. 로컬에선 자격이 없어 막히지만
EC2 컨테이너 안에서는 IAM Role로 Bedrock 호출이 된다.

실행(컨테이너 내부):
    docker compose exec -T app python -m ci.check_ec2_flow

각 섹션은 독립 try/except로, 한 곳이 실패해도 나머지를 끝까지 점검한다.
"""
import asyncio
import traceback


def section(title: str) -> None:
    print("\n" + "=" * 56)
    print(title)
    print("=" * 56)


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def check_identity() -> None:
    section("1. AWS 자격 식별 (EC2 Role)")
    try:
        import boto3
        arn = boto3.client("sts", region_name="ap-northeast-2").get_caller_identity()["Arn"]
        ok(f"identity = {arn}")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


def check_embedding() -> None:
    section("2. Bedrock 임베딩 (Cohere Embed)")
    try:
        from BACK.backend.llm.embedding_factory import embed_query
        vec = embed_query("UFLPA 강제노동 수입금지 규정")
        ok(f"임베딩 차원 = {len(vec)} (schema VECTOR(1536)와 일치해야 함)")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


def check_llm() -> None:
    section("3. Bedrock LLM (Claude Sonnet)")
    try:
        from BACK.backend.llm.bedrock_factory import get_llm, Model
        resp = get_llm(Model.SONNET_46, max_tokens=64).invoke("Reply with exactly: OK")
        ok(f"응답 = {str(resp.content)[:80]!r}")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


async def _db_checks() -> None:
    from sqlalchemy import text
    from BACK.backend.infrastructure.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        for t in (
            "suppliers", "products", "batches", "supply_chain_map",
            "regulations", "compliance_results", "document_extraction_results",
            "verification_results", "geo_audit_results",
        ):
            try:
                n = (await db.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
                ok(f"{t}: {n} rows")
            except Exception as e:
                await db.rollback()  # 트랜잭션 복구 — 다음 쿼리가 연쇄 abort되지 않게
                fail(f"{t}: {type(e).__name__}: {str(e).splitlines()[0]}")

        # 규제 임베딩 적재 상태 — 배선(부팅 자동 시드)이 동작하면 indexed:10
        try:
            rows = (await db.execute(text(
                "SELECT embedding_status, COUNT(*) FROM regulations GROUP BY embedding_status"
            ))).all()
            ok("regulations.embedding_status = " + ", ".join(f"{s}:{c}" for s, c in rows))
        except Exception as e:
            await db.rollback()
            fail(f"embedding_status: {type(e).__name__}: {str(e).splitlines()[0]}")


def check_db() -> None:
    section("4. DB 연결 + 시드 적재 + RAG 준비")
    try:
        asyncio.run(_db_checks())
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    print("KIRA — EC2 데이터흐름 / Bedrock 점검")
    check_identity()
    check_embedding()
    check_llm()
    check_db()
    print("\n점검 완료.")
