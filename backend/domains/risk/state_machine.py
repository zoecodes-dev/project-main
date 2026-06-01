from backend.domains.risk.models import RiskProfile


def calculate_risk_level(score: int) -> str:
    """
    위험 점수(0~100)를 기반으로 리스크 레벨 구간을 판정합니다.
    가점식이며 높을수록 위험합니다.
    - 70~100: critical
    - 50~69: high
    - 30~49: medium
    - 0~29: low
    """
    if score >= 70:
        return "critical"
    elif score >= 50:
        return "high"
    elif score >= 30:
        return "medium"
    return "low"


def update_risk_profile_state(profile: RiskProfile, new_score: int, additional_reasons: list[str]) -> bool:
    """
    리스크 프로필의 상태(점수, 레벨, 플래그, 사유)를 일관되게 전이합니다.
    에스컬레이션(HITL 강제 회부)이 필요한 critical 상태인 경우 True를 반환합니다.
    """
    profile.overall_risk_score = min(100, max(0, new_score))
    profile.risk_level = calculate_risk_level(profile.overall_risk_score)
    
    # risk_level이 high 또는 critical이면 고위험 플래그 활성화
    profile.is_high_risk_flag = profile.risk_level in ("high", "critical")
    profile.high_risk_reasons = additional_reasons
    
    # 70점(critical) 이상이면 에스컬레이션(HITL) 필요
    return profile.risk_level == "critical"
