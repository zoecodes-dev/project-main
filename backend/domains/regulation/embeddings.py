"""
backend/domains/regulation/embeddings.py

규제 임베딩 멱등 시드 — embedding_status='pending'인 것만 임베딩하고 'indexed'는 건너뛴다.
부팅 시 1회 실행(docker-compose app command). 이미 indexed면 SELECT 1번 후 즉시 종료 →
Bedrock 호출 0, 재계산 없음. wipe 없이 운영되는 한 임베딩은 영구 유지된다.

실행: python -m backend.domains.regulation.embeddings
"""
import asyncio
import logging

from sqlalchemy import select, text

from backend.infrastructure.database import AsyncSessionLocal
from backend.domains.regulation.models import Regulation
from backend.llm.embedding_factory import embed_query

log = logging.getLogger(__name__)


async def reindex_pending_embeddings() -> int:
    """pending 규제만 임베딩 → indexed. 처리 건수 반환(0이면 할 일 없음)."""
    async with AsyncSessionLocal() as db:
        pending = (await db.execute(
            select(Regulation).where(Regulation.embedding_status == "pending")
        )).scalars().all()

        if not pending:
            log.info("규제 임베딩: pending 0건 — 스킵(이미 indexed, 재계산 없음)")
            return 0

        done = 0
        for reg in pending:
            try:
                vec = embed_query(f"{reg.regulation_code} {reg.name} {reg.description or ''}")
            except Exception as e:
                # 로컬 폴백 — AWS 자격 없을 때 가짜 임베딩 (sha256 시드, 1536-dim)
                import hashlib, random
                log.warning("규제 %s Bedrock 실패, 로컬 폴백 사용: %s", reg.regulation_code, e)
                seed = int(hashlib.sha256(reg.regulation_code.encode()).hexdigest(), 16)
                vec = [random.Random(seed).uniform(-1, 1) for _ in range(1536)]

            vec_str = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            await db.execute(
                text("UPDATE regulations SET embedding = (:vec)::vector, "
                    "embedding_status = 'indexed' WHERE regulation_id = :id"),
                {"vec": vec_str, "id": str(reg.regulation_id)},
)
            done += 1

        await db.commit()
        log.info("규제 임베딩: %d/%d건 indexed 완료", done, len(pending))
        return done


if __name__ == "__main__":
    # ★ 부팅을 막지 않는다 — 로컬엔 Bedrock 자격이 없을 수 있으므로 실패해도 경고만, exit 0.
    try:
        asyncio.run(reindex_pending_embeddings())
    except Exception as e:
        log.warning("규제 임베딩 시드 스킵(부팅 계속): %s", e)