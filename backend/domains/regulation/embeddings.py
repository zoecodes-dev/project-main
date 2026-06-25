"""
backend/domains/regulation/embeddings.py

규제 임베딩 멱등 시드 — embedding_status='pending'인 것만 임베딩하고 'indexed'는 건너뛴다.
부팅 시 1회 실행(docker-compose app command). 이미 indexed면 SELECT 1번 후 즉시 종료 →
Bedrock 호출 0, 재계산 없음. wipe 없이 운영되는 한 임베딩은 영구 유지된다.

실행: python -m backend.domains.regulation.embeddings
"""
import asyncio
import logging

from sqlalchemy import select, update

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
                await db.execute(
                    update(Regulation)
                    .where(Regulation.regulation_id == reg.regulation_id)
                    .values(embedding=vec, embedding_status="indexed")
                )
                done += 1
            except Exception as e:
                # 한 건 실패는 다음 부팅 때 재시도(여전히 pending이므로).
                log.warning("규제 %s 임베딩 실패: %s", reg.regulation_code, e)

        await db.commit()
        log.info("규제 임베딩: %d/%d건 indexed 완료", done, len(pending))
        return done


if __name__ == "__main__":
    # ★ 부팅을 막지 않는다 — 로컬엔 Bedrock 자격이 없을 수 있으므로 실패해도 경고만, exit 0.
    try:
        asyncio.run(reindex_pending_embeddings())
    except Exception as e:
        log.warning("규제 임베딩 시드 스킵(부팅 계속): %s", e)