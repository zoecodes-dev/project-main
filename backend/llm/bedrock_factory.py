"""
KIRA — Bedrock 모델 팩토리
=========================
LangGraph 에이전트들이 공통으로 쓸 LLM 인스턴스 생성기.

인증: EC2에 부착된 IAM Role(KIRA-EC2-Bedrock-Role)이 자동 처리.
      코드에 키를 절대 넣지 않는다. boto3가 인스턴스 메타데이터에서
      자격증명을 자동으로 가져온다.

모델 ID: 최신 Claude는 추론 프로파일(CRIS) ID를 써야 한다.
         서울(ap-northeast-2)에서 list-inference-profiles로 확인한
         global. 접두사 ID를 사용한다. 접두사 없는 기본 모델 ID로는
         호출이 거부될 수 있다.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from langchain_aws import ChatBedrockConverse


# ─────────────────────────────────────────────────────────
# 리전 — EC2와 동일 리전(서울). Bedrock 호출도 여기서 나간다.
# ─────────────────────────────────────────────────────────
AWS_REGION = "ap-northeast-2"


# ─────────────────────────────────────────────────────────
# 모델 카탈로그 — 검증된 추론 프로파일 ID (2026-06 기준)
# 새 모델 추가 시 list-inference-profiles로 ID 확인 후 등록.
# ─────────────────────────────────────────────────────────
class Model(str, Enum):
    OPUS_48 = "global.anthropic.claude-opus-4-8"
    OPUS_47 = "global.anthropic.claude-opus-4-7"
    SONNET_46 = "global.anthropic.claude-sonnet-4-6"
    HAIKU_45 = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


# ─────────────────────────────────────────────────────────
# 에이전트 → 모델 매핑
# 모델 배분은 팀에서 결정한 값. 여기 한 곳에서만 관리한다.
# (실제 배분에 맞게 팀에서 수정)
# ─────────────────────────────────────────────────────────
AGENT_MODEL_MAP: dict[str, Model] = {
    "supervisor": Model.HAIKU_45,     # 지혜
    "data_gateway": Model.SONNET_46,    # 은진 (Zoe)
    "compliance": Model.SONNET_46,       # 은지 — 컴플라이언스 해석, 정확도 핵심
    "geo_audit": Model.SONNET_46,   # 영수
    "automation": Model.HAIKU_45,   # 차윤 — 자동화 컨트롤
    # 경량 작업용 (알림/파싱 등)
    "lightweight": Model.HAIKU_45,
}
  

@lru_cache(maxsize=16)
def get_llm(
    model: Model = Model.SONNET_46,
    *,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> ChatBedrockConverse:
    """
    Bedrock LLM 인스턴스 반환. 같은 (model, params) 조합은 캐시 재사용.

    인증은 IAM Role이 자동 처리 — credentials 인자를 넘기지 않는다.
    """
    return ChatBedrockConverse(
        model=model.value,
        region_name=AWS_REGION,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_llm_for_agent(agent: str, **kwargs) -> ChatBedrockConverse:
    """에이전트 이름으로 매핑된 모델의 LLM을 반환."""
    model = AGENT_MODEL_MAP.get(agent, Model.SONNET_46)
    return get_llm(model, **kwargs)


# ─────────────────────────────────────────────────────────
# 연결 확인용 (배포 직후 IAM Role + Bedrock 호출이 되는지 검증)
# 실행:  python -m backend.llm.bedrock_factory
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    print(f"[KIRA] Bedrock 연결 테스트 (region={AWS_REGION})")
    try:
        llm = get_llm(Model.SONNET_46, max_tokens=128)
        resp = llm.invoke("한 문장으로 자기소개 해줘. 모델명도 포함해서.")
        print("─" * 50)
        print("응답:", resp.content)
        print("─" * 50)
        print("✅ Bedrock 호출 성공 — IAM Role 인증 정상")
    except Exception as e:
        print("❌ Bedrock 호출 실패")
        print(f"   에러: {type(e).__name__}: {e}")
        print("   점검: 1) IAM Role 부착 여부  2) 모델 프로파일 ID")
        print("        3) 첫 호출 시 use case 입력 필요할 수 있음(콘솔 Playground)")
        sys.exit(1)
