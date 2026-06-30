"""
domains/report/summary_templates.py

공급망 리스크 관리 요약문 — locale별 문장 템플릿 + 렌더러.

설계 메모:
  - 요약문은 "집계 숫자 → 템플릿 렌더링" 방식이다(자유 텍스트/LLM 아님).
    같은 metrics로 KO/EN/DE를 결정론적으로 재렌더할 수 있어 다국어 확장이 쉽다.
  - A 단계: "ko"만 구현. en/de 요청은 ko로 fallback(아래 _pick).
    B 단계에서 TEMPLATES["en"], TEMPLATES["de"]만 추가하면 끝.
  - 방어 규칙:
      · supplier_total == 0      → "집계할 데이터 없음" 단일 문장
      · audited_suppliers == 0   → 실사 문장 생략
      · capa_total == 0          → 시정조치(CAPA) 문장 생략
"""
from typing import Dict, List

DEFAULT_LOCALE = "ko"

# locale → 문장 조각. 모든 조각은 동일 키 구조를 가지므로,
# B 단계에서 "en"/"de" 블록만 같은 키로 추가하면 된다.
TEMPLATES: Dict[str, Dict[str, str]] = {
    "ko": {
        "title": "공급망 리스크 관리 현황",
        "empty": "아직 등록된 협력사가 없어 리스크 관리 현황을 집계할 데이터가 없습니다.",
        "suppliers_high": (
            "현재 {supplier_total}개 협력사를 관리하고 있으며, 이 중 고위험(high·critical) "
            "협력사 {high_risk_count}곳을 실사·시정조치 대상으로 집중 관리하고 있습니다."
        ),
        "suppliers_clean": (
            "현재 {supplier_total}개 협력사를 관리하고 있으며, "
            "고위험(high·critical)으로 분류된 협력사는 없습니다."
        ),
        "audit": "실사를 수행한 {audited_suppliers}개 협력사의 적합 판정률은 {audit_pass_rate}%입니다.",
        "capa": "진행된 시정조치(CAPA) {capa_total}건 중 {capa_closed}건({capa_rate}%)이 완료되었습니다.",
        "compliance": (
            "컴플라이언스 통과율은 {compliance_pass_rate}%, 공급망 연결의 "
            "{chain_verified_rate}%가 검증 완료 상태로 지속 추적·갱신되고 있습니다."
        ),
        # ── 핵심 포인트(결재함/스냅샷용 bullet) ──
        "kp_high_risk": "고위험 협력사 {high_risk_count}곳",
        "kp_capa": "시정조치 완료율 {capa_rate}%",
        "kp_compliance": "컴플라이언스 통과율 {compliance_pass_rate}%",
        "kp_chain": "공급망 검증율 {chain_verified_rate}%",
    },
    "en": {
        "title": "Supply Chain Risk Management Status",
        "empty": "No suppliers are registered yet, so there is no risk management data to report.",
        "suppliers_high": (
            "We currently manage {supplier_total} suppliers, of which {high_risk_count} "
            "are classified as high-risk (high/critical) and are under focused "
            "due-diligence and corrective action."
        ),
        "suppliers_clean": (
            "We currently manage {supplier_total} suppliers, with none classified "
            "as high-risk (high/critical)."
        ),
        "audit": "Across the {audited_suppliers} suppliers audited, the pass rate is {audit_pass_rate}%.",
        "capa": "Of {capa_total} corrective actions (CAPA), {capa_closed} ({capa_rate}%) have been completed.",
        "compliance": (
            "The compliance pass rate is {compliance_pass_rate}%, and {chain_verified_rate}% "
            "of supply-chain links are verified and continuously tracked."
        ),
        "kp_high_risk": "High-risk suppliers: {high_risk_count}",
        "kp_capa": "CAPA completion rate: {capa_rate}%",
        "kp_compliance": "Compliance pass rate: {compliance_pass_rate}%",
        "kp_chain": "Supply-chain verification rate: {chain_verified_rate}%",
    },
    "de": {
        "title": "Status des Lieferketten-Risikomanagements",
        "empty": "Es sind noch keine Lieferanten registriert, daher liegen keine Risikomanagement-Daten vor.",
        "suppliers_high": (
            "Wir betreuen derzeit {supplier_total} Lieferanten, davon sind {high_risk_count} "
            "als hochriskant (high/critical) eingestuft und werden gezielt durch "
            "Sorgfaltsprüfungen und Korrekturmaßnahmen überwacht."
        ),
        "suppliers_clean": (
            "Wir betreuen derzeit {supplier_total} Lieferanten, wobei keiner "
            "als hochriskant (high/critical) eingestuft ist."
        ),
        "audit": "Bei den {audited_suppliers} geprüften Lieferanten liegt die Bestehensquote bei {audit_pass_rate}%.",
        "capa": "Von {capa_total} Korrekturmaßnahmen (CAPA) sind {capa_closed} ({capa_rate}%) abgeschlossen.",
        "compliance": (
            "Die Compliance-Bestehensquote beträgt {compliance_pass_rate}%, und {chain_verified_rate}% "
            "der Lieferketten-Verbindungen sind verifiziert und werden kontinuierlich nachverfolgt."
        ),
        "kp_high_risk": "Hochriskante Lieferanten: {high_risk_count}",
        "kp_capa": "CAPA-Abschlussquote: {capa_rate}%",
        "kp_compliance": "Compliance-Bestehensquote: {compliance_pass_rate}%",
        "kp_chain": "Lieferketten-Verifizierungsquote: {chain_verified_rate}%",
    },
}

# ── 고객사 국가 → 전송 언어 결정 ────────────────────────────────────────────
# 기본 EN. 독일(DE)이면 DE 추가. country 미상이면 EN + country_known=False(사람이 선택).
GERMANY = "DE"


def resolve_outbound_locales(country: str | None) -> list[str]:
    """고객사 country(ISO alpha-2) → 전송할 locale 목록. 독일이면 EN+DE, 그 외 EN."""
    if country and country.strip().upper() == GERMANY:
        return ["en", "de"]
    return ["en"]


def _pick(locale: str) -> Dict[str, str]:
    """미구현 locale(en/de)은 default(ko)로 fallback. B 붙이면 자동으로 실제 번역."""
    return TEMPLATES.get(locale) or TEMPLATES[DEFAULT_LOCALE]


def section_title(locale: str = DEFAULT_LOCALE) -> str:
    return _pick(locale)["title"]


def render_summary(metrics: Dict[str, int], locale: str = DEFAULT_LOCALE) -> str:
    """metrics → 2~4문장 요약 본문. 데이터 부족 시 해당 문장을 조용히 생략."""
    t = _pick(locale)

    if metrics["supplier_total"] == 0:
        return t["empty"]

    parts: List[str] = []
    key = "suppliers_high" if metrics["high_risk_count"] > 0 else "suppliers_clean"
    parts.append(t[key].format(**metrics))

    # 판정 완료된 실사가 있을 때만 적합률 노출(전부 pending이면 "0%" 오해 방지).
    if metrics["audit_decided"] > 0:
        parts.append(t["audit"].format(**metrics))
    if metrics["capa_total"] > 0:
        parts.append(t["capa"].format(**metrics))

    parts.append(t["compliance"].format(**metrics))
    return " ".join(parts)


def render_key_points(metrics: Dict[str, int], locale: str = DEFAULT_LOCALE) -> List[str]:
    """결재함 표시 및 전송 스냅샷용 핵심 포인트 bullet 목록(있는 항목만)."""
    if metrics["supplier_total"] == 0:
        return []

    t = _pick(locale)
    bullets: List[str] = []
    if metrics["high_risk_count"] > 0:
        bullets.append(t["kp_high_risk"].format(**metrics))
    if metrics["capa_total"] > 0:
        bullets.append(t["kp_capa"].format(**metrics))
    bullets.append(t["kp_compliance"].format(**metrics))
    bullets.append(t["kp_chain"].format(**metrics))
    return bullets
