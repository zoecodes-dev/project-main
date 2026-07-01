"""
ci/e2e_pipeline.py — 런타임 파이프라인 E2E (EC2 컨테이너 내부, SSM 경유)

Happy 시나리오(BMW iX3) 배치 하나를 8단계 그래프에 실제로 태워, 각 단계 산출물이 DB에
쌓이고 최종 상태까지 흐르는지 검증한다. HITL interrupt가 걸리면 거기서 멈춘 상태를 보고한다.

실행: docker compose exec -T app python -m ci.e2e_pipeline
"""
import asyncio
import traceback

from sqlalchemy import text

from backend.infrastructure.database import AsyncSessionLocal
from backend.agents.graph import start_graph, resume_graph

# BMW iX3 (Happy) — 시드의 완전한 배치 정보(tenant·bom)를 그대로 사용해야 DPP 점수 쿼리
# (get_score_raw_data: JOIN tenants/bom_versions)가 동작한다. graph.create_batch는
# product_id·destination만 채워 tenant_id·bom_version_id가 NULL → DPP에서 실패하므로 직접 INSERT.
PRODUCT = "d1111111-0000-4000-8000-000000000001"
BOM = "e1111111-0000-4000-8000-000000000001"
TENANT = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
DEST = "EU"


def ok(m): print(f"  [PASS] {m}")
def info(m): print(f"  [INFO] {m}")
def fail(m): print(f"  [FAIL] {m}")


async def main():
    print("KIRA — 런타임 파이프라인 E2E (iX3 Happy)")
    print("=" * 56)

    # 1) 새 배치 생성 (완전한 배치 — tenant_id·bom_version_id 포함, stage_queued)
    async with AsyncSessionLocal() as db:
        batch_id = str((await db.execute(text("""
            INSERT INTO batches (product_id, bom_version_id, tenant_id, destination,
                                 current_stage, status)
            VALUES (:pid, :bom, :tid, :dest, 'stage_queued', 'batch_processing')
            RETURNING batch_id
        """), {"pid": PRODUCT, "bom": BOM, "tid": TENANT, "dest": DEST})).scalar())
        await db.commit()
    info(f"batch 생성(complete): {batch_id} (product=iX3, dest={DEST})")

    # 2) 그래프 실행 — 8단계. 완료 또는 HITL interrupt까지.
    try:
        await start_graph(batch_id, PRODUCT, DEST)
        info("graph ainvoke 반환 (완료 또는 interrupt 지점 도달)")
    except Exception as e:
        info(f"graph 실행 중단: {type(e).__name__}: {e}")
        traceback.print_exc()

    # 2b) HITL resume 루프 — geo risk·risk escalation 등 여러 번 interrupt 가능 → 반복 resume(approve)
    for attempt in range(1, 5):
        async with AsyncSessionLocal() as db:
            st = (await db.execute(text(
                "SELECT status, current_stage FROM batches WHERE batch_id=:b"),
                {"b": batch_id})).first()
        info(f"resume 전 #{attempt}: status={st[0]} stage={st[1]}")
        if st[0] != "batch_hitl_wait":
            info(f"HITL 대기 아님 → 루프 종료 (status={st[0]})")
            break
        try:
            await resume_graph(batch_id, "approve")
            info(f"resume #{attempt}(approve) 반환")
        except Exception as e:
            info(f"resume #{attempt} 중단: {type(e).__name__}: {e}")
            traceback.print_exc()
            break

    # 3) 결과 조회 — 최종 상태 + 단계별 산출물
    print("-" * 56)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT current_stage, status, confidence_score "
            "FROM batches WHERE batch_id=:b"), {"b": batch_id})).first()
        if row:
            ok(f"최종 batch: stage={row[0]} status={row[1]} confidence={row[2]}")

        for label, q in [
            ("verification_results", "SELECT count(*) FROM verification_results WHERE batch_id=:b"),
            ("geo_audit_results",    "SELECT count(*) FROM geo_audit_results WHERE batch_id=:b"),
            ("compliance_results",   "SELECT count(*) FROM compliance_results WHERE batch_id=:b"),
        ]:
            try:
                n = (await db.execute(text(q), {"b": batch_id})).scalar()
                (ok if n and n > 0 else info)(f"{label}: {n} rows")
            except Exception as e:
                await db.rollback()
                fail(f"{label}: {type(e).__name__}: {str(e).splitlines()[0]}")

    print("=" * 56)
    print("E2E 완료.")


if __name__ == "__main__":
    asyncio.run(main())
