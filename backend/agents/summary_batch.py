def render_summary(verdict, metrics, locale="ko"):
    violation = int(metrics.get("violation", 0) or 0)
    warning = int(metrics.get("warning", 0) or 0)
    passed = int(metrics.get("passed", 0) or 0)
    risk_level = metrics.get("risk_level")
    risk_score = int(metrics.get("risk_score", 0) or 0)

    risk_mentioned = False
    if verdict == "fail":
        if violation > 0:
            sentences = [f"이 배치는 규제 위반 {violation}건으로 부적합(fail) 판정입니다."]
        elif risk_level == "critical":
            sentences = [f"이 배치는 공급망 위험등급 critical(점수 {risk_score})로 부적합(fail) 판정입니다."]
            risk_mentioned = True
        else:
            sentences = ["이 배치는 고위험 신호로 부적합(fail) 판정입니다."]
    elif verdict == "conditional":
        sentences = [f"이 배치는 회색지대/위험 신호({warning}건)로 조건부(conditional) 판정입니다."]
    else:
        sentences = [f"이 배치는 규제 {passed}건을 모두 통과해 적합(pass) 판정입니다."]

    geo_flags = metrics.get("geo_flags") or []
    if geo_flags:
        sentences.append(f"지리 위험: {', '.join(str(flag) for flag in geo_flags)}.")

    if not risk_mentioned and risk_level in ("high", "critical"):
        sentences.append(f"공급망 위험등급 {risk_level}(점수 {risk_score}).")

    return " ".join(sentences)


def render_key_risks(metrics):
    risks = []
    violation = int(metrics.get("violation", 0) or 0)
    warning = int(metrics.get("warning", 0) or 0)
    geo_flags = metrics.get("geo_flags") or []
    risk_level = metrics.get("risk_level")

    if violation:
        risks.append(f"규제 위반 {violation}건")
    if warning:
        risks.append(f"회색지대/위험 신호 {warning}건")
    for flag in geo_flags:
        risks.append(f"지리 위험: {flag}")
    if risk_level in ("high", "critical"):
        risk_score = int(metrics.get("risk_score", 0) or 0)
        risks.append(f"공급망 위험등급 {risk_level}(점수 {risk_score})")

    return risks[:5]
