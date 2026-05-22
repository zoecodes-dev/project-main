import pytest
from uuid import uuid4
from backend.domains.audit.service import create_audit_entry


@pytest.mark.asyncio
async def test_create_audit_entry_returns_expected():
    batch_id = uuid4()
    result = await create_audit_entry(
        db=None,  # db 없으면 trace_node가 기록 생략하고 통과
        batch_id=batch_id,
        decision_text="테스트 결정",
    )
    assert result["batch_id"] == str(batch_id)
    assert result["status"] == "recorded"