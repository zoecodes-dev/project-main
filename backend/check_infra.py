
import os
import sys
import asyncio
import uuid

# 1. 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 🌟 2. [가장 중요] backend 인프라 모듈을 import 하기 "전에" 로컬 주소로 덮어씌웁니다!
# (주의: 비밀번호 부분은 실제 .env에 있는 POSTGRES_PASSWORD 값으로 적어주세요)
os.environ["DATABASE_URL"] = "postgresql+asyncpg://kira_admin:kira_secure_pass@localhost:5433/kira_db"
os.environ["REDIS_URL"] = "redis://localhost:6380/0"

# 3. 이제 인프라 모듈들을 import 합니다. 
# (이때 config.py가 실행되면서 위에서 덮어씌운 localhost 주소를 가져가게 됩니다)
from infrastructure.database import AsyncSessionLocal as async_session_factory
from infrastructure.trace import trace_node
from infrastructure.event_bus import publish
from infrastructure.queue import enqueue

# 1. trace.py 검증용 에이전트 노드 데코레이터 지정
@trace_node(node_name="infra_health_check_node", node_type="agent")
async def dummy_infra_node(state: dict, db):
    return {**state, "infrastructure_status": "verified"}

async def main():
    print("=== KIRA 인프라 백본 4대장 연동 테스트 ===")
    
    # [체크 1] database.py & PostgreSQL 커넥션 확인
    try:
        async with async_session_factory() as db:
            print("✅ 1. database.py: PostgreSQL 연결 및 세션 생성 성공")
            
            # [체크 2] trace.py & 해시 체인 DB 인서트 확인
            test_batch_id = None
            initial_state = {"batch_id": test_batch_id, "step": "start"}
            
            # 데코레이터가 붙은 함수를 실행 (db는 kwargs로 명확하게 전달)
            final_state = await dummy_infra_node(initial_state, db=db)
            print("✅ 2. trace.py: @trace_node 데코레이터 및 DB 인서트 프로세스 정상")
            
    except Exception as e:
        print(f"❌ DB/Trace 연동 실패: {e}")
        return

    # [체크 3] event_bus.py 깡통 함수 확인
    try:
        # import 된 publish 함수를 바로 호출
        await publish(db, "SupplierInvited", {"supplier_id": "test-id"})
        print("✅ 3. event_bus.py: 이벤트 발행(publish) 인터페이스 확인")
    except Exception as e:
        print(f"❌ event_bus.py 에러: {e}")

    # [체크 4] queue.py 깡통 함수 확인
    try:
        # queue_name("ocr_queue") 필수, func_name("ocr_task"), **kwargs 형태
        await enqueue("ocr_queue", "ocr_task", file_url="http://example.com/pdf")
        print("✅ 4. queue.py: 비동기 큐 작업 예약(enqueue) 인터페이스 확인")
    except Exception as e:
        print(f"❌ queue.py 에러: {e}")

    print("\n🎉 [결과] 4대 인프라 기본 인터페이스 검증 완료!")

    # check_infra.py 맨 아래쪽 수정
    from infrastructure.queue import get_redis_pool
    redis_pool = await get_redis_pool()
    if redis_pool:
        # 확실하게 풀(Pool) 연결을 해제합니다.
        await redis_pool.connection_pool.disconnect()

if __name__ == "__main__":
    asyncio.run(main())