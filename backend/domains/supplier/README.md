# Supplier Domain (협력사 마스터 및 리스크 제어)

KIRA 플랫폼의 가장 하단 레이어인 **공급망 데이터 백본(Layer 1)** 중 협력사 마스터 데이터, 사업장(공장/광산) 정보, 리스크 프로필 및 원산지 증명서를 관리하는 핵심 도메인입니다.

## 1. 담당 영역 및 관리 테이블

Supplier 도메인은 협력사 마스터 데이터와 그 부속 정보의 비즈니스 로직·ORM을 총괄합니다.
**테이블 구성의 SSOT는 `docker/01_schema.sql`·`models.py`이며, 프로젝트 스코프에 따라 변동**합니다
(개수·목록을 이 문서에 고정하지 않음).

핵심(현행) 영역:
* **협력사 마스터**: `suppliers`, `supplier_contacts`(PIC), `supplier_factories`, `supplier_onboarding`
* **Provider Type별 상세 (CTI)**: `suppliers`를 부모로 두는 자식 테이블. 현재 `supplier_manufacturer_details`·`supplier_miner_details` 중심(적용 provider_type에 따라 변동)
* **협력사 리스크 프로필**: `supplier_risk_profiles` (+ 감사/공장 탄소선언 등 연관 테이블)

> ⚠️ **스코프 변동 주의**: 재활용사·트레이더·원산지 증명서·교육·인권/산재 등 일부 영역은 현재 스코프에서 제외(관련 테이블 삭제)되었습니다. 되살아날 수 있으므로 항상 `01_schema.sql`을 SSOT로 확인하세요.

---

## 2. 불변 코어 설계 원칙 (PROJECT_CORE.md 반영)

Supplier 도메인을 확장하거나 디벨롭할 때 다른 팀원과 LLM은 반드시 다음 규칙을 준수해야 합니다. 위반 시 PR이 반려됩니다.

1.  **Provider Type CTI 분리 준수**
    * provider_type별로 요구하는 규제 서류·데이터 구조가 다릅니다. 이를 단일 테이블로 통합하지 말고, `suppliers`를 부모로 두는 CTI(Class Table Inheritance) 자식 테이블 구조를 유지하십시오.
    * 현재 활성 CTI는 `manufacturer`·`miner` 중심입니다(적용 provider_type/스코프에 따라 변동 — 코드 `_CTI_ATTR_BY_TYPE`가 SSOT).
2.  **원산지 추적의 최소 단위는 '공장(Factory)'**
    * 회사의 본사 주소는 컴플라이언스 판정 기준이 될 수 없습니다.
    * `supplier_factories.location`에 저장되는 **PostGIS POINT 좌표**와 국가 코드가 영수 에이전트(Geo Audit)의 위성 분석 및 규제 지역 판정의 단일 진실 공급원(SSOT)입니다.
3.  **트레이더(Trader) 정보 공개 제어 게이트** — ⚠️ *현재 스코프 제외(parked)*
    * (원설계) 중개상이 상위 공급망을 투명 공개하지 않으면 위험 플래그. 관련 테이블(`trader_disclosure_obligation`)이 현 스코프에서 제거되어 **비활성**. 트레이더 범위 복원 시 재도입.
4.  **감사 추적(Provenance) 강제**
    * 협력사의 상태(`status`)나 리스크 레벨(`risk_level`)을 변경하는 모든 핵심 비즈니스 함수에는 `infrastructure/trace.py`에 정의된 `@trace_node` 데코레이터를 필수로 적용해야 합니다.

---

## 3. 구현 상태 및 진행 이력

### 📌 W1~W2 마일스톤 (완료)
* **Models (`models.py`)**: `schema.sql` 명세와 100% 일치하는 SQLAlchemy 2.0 ORM 모델 맵핑 (PostGIS POINT 타입 연동 포함).
* **마스터 CRUD 및 상태머신**: 협력사 등록 뼈대 구축 및 상태가 임의로 점프하지 못하도록 제어하는 매트릭스 완성.
* **이벤트 기반 아키텍처**: `SupplierInvited` 등 도메인 간 결합도를 낮추는 이벤트 발행/구독(Publish) 레퍼런스 구조 완성.
* **조회 최적화**: Repository 계층에서 N+1 문제를 방지하는 단건/목록 Eager Loading (협력사 + 공장 + CTI 상세) 적용.

### 📌 W3 마일스톤 (완료 및 진행중)
* **에이전트 파이프라인 연동 (`data_gateway`)**: 공급망 트리(`SupplyChainRepository`)를 통해 N-tier 협력사 목록을 추출하고, 연관된 `document_extraction_results`를 모아 신뢰도를 검증하는 진입점 로직 연동 완료.
* **워커 멱등성**: S3에서 비공개 문서를 읽어 Bedrock Vision으로 파싱하는 `document_parse_worker` 작업 완료.
* **지리 공간 인덱스**: 영수 에이전트 전용 PostGIS 헬퍼 함수 개발 지원 진행 중.

## 4. 향후 로드맵 (W4)
* `supplier_risk_profiles` 기반 종합 리스크 스코어링 감점식 엔진 구현.
* Human-In-The-Loop (HITL) 인터럽트와 협력사 리스크 상태 연동

---

## 5. 흐름 연결 배선 (초대·동의·수집·승격)

원청↔협력사 흐름(초대→가입→자료수집→승인)을 실데이터로 잇는 supplier 도메인 기능:

* **협력사 초대 + PIC 저장**: `POST /suppliers`(`create_supplier_and_invite`) — stub 생성 + `SupplierInvited` 발행(초대 메일 SES + `supply_chain_map.discovered_via`) + PIC를 `supplier_contacts`에 같은 트랜잭션 저장(다음 화면 재표기용). body: `inviter_supplier_id`(상위 협력사, 원청 직접이면 null) + `contacts[]`.
* **제3자 정보제공 동의 게이트**: `require_supplier_consent`(infrastructure/auth) — 협력사 계정이 `supplier_onboarding.consent_status='consent_agreed'` 전이면 데이터 입력(`master-form`·`PATCH /detail`)에서 403 `CONSENT_REQUIRED`. OEM/감사자는 통과.
* **온보딩(URL 초대 진입)**: `GET .../onboarding/prefill`(회사·타입 미리채움) + `POST .../onboarding/submit`(회사·PIC·문서·동의·계정 생성) — stub을 UPDATE(회원가입 전 저장 → 가입 즉시 진행).
* **AI 추출값 승격**: `promote_extraction_to_details` — 배치 게이트(`data_gateway`) 통과 시 협력사 확정 추출값을 provider_type별 상세테이블로 승격(`masterform_prefill` 매핑 재사용). 승격 대상 필드는 규제/스코프에 따라 변동(코드가 SSOT).
* **메일 발송(SES)**: `infrastructure/mail.py`(안전 no-op 스위치, `MAIL_ENABLED`/`MAIL_FROM`) + notification 워커 email 채널.