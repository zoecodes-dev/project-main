# Supplier Domain (협력사 마스터 및 리스크 제어)

KIRA 플랫폼의 가장 하단 레이어인 **공급망 데이터 백본(Layer 1)** 중 협력사 마스터 데이터, 사업장(공장/광산) 정보, 리스크 프로필 및 원산지 증명서를 관리하는 핵심 도메인입니다.

## 1. 담당 영역 및 관리 테이블 (schema.sql 기준)

Supplier 도메인은 `schema.sql` 내의 다음 5개 영역, 총 14개 테이블의 비즈니스 로직 및 ORM 모델 관리를 총괄합니다.

* **영역 2. 협력사 마스터**: `suppliers`, `supplier_contacts`, `supplier_factories`, `supplier_onboarding`, `supplier_certifications`
* **영역 3. Provider Type별 상세 (CTI)**: `supplier_manufacturer_details`, `supplier_recycler_details`, `supplier_trader_details`, `supplier_miner_details`, `trader_disclosure_obligation`
* **영역 4. 협력사 리스크 프로필**: `supplier_risk_profiles`, `supplier_audit_records`, `supplier_human_rights_issues`, `supplier_industrial_accidents`
* **영역 5. 원산지 증명서**: `origin_certificates`
* **영역 6. 교육 관리**: `training_materials`, `training_records`

---

## 2. 불변 코어 설계 원칙 (PROJECT_CORE.md 반영)

Supplier 도메인을 확장하거나 디벨롭할 때 다른 팀원과 LLM은 반드시 다음 규칙을 준수해야 합니다. 위반 시 PR이 반려됩니다.

1.  **Provider Type CTI 분리 준수**
    * 제조사, 재활용사, 중개상, 광산(`manufacturer`, `recycler`, `trader`, `miner`)은 요구하는 규제 서류와 데이터 구조가 완전히 다릅니다.
    * 이를 절대 단일 테이블로 통합하지 말고, `suppliers` 테이블을 부모로 두는 CTI(Class Table Inheritance) 자식 테이블 구조를 유지하십시오.
2.  **원산지 추적의 최소 단위는 '공장(Factory)'**
    * 회사의 본사 주소는 컴플라이언스 판정 기준이 될 수 없습니다.
    * `supplier_factories.location`에 저장되는 **PostGIS POINT 좌표**와 국가 코드가 영수 에이전트(Geo Audit)의 위성 분석 및 규제 지역 판정의 단일 진실 공급원(SSOT)입니다.
3.  **트레이더(Trader) 정보 공개 제어 게이트**
    * 공급망 중간에 끼어있는 중개상이 상위 공급망을 투명하게 공개하지 않을 경우(`trader_disclosure_obligation.disclosure_completeness < 75%`), 차윤 에이전트는 해당 라인의 DPP 발행을 자동 차단해야 합니다.
4.  **감사 추적(Provenance) 강제**
    * 협력사의 상태(`status`)나 리스크 레벨(`risk_level`)을 변경하는 모든 핵심 비즈니스 함수에는 `infrastructure/trace.py`에 정의된 `@trace_node` 데코레이터를 필수로 적용해야 합니다.

---

## 3. 1주차 구현 상태 (W1 Milestone)

* **Models (`models.py`)**: `schema.sql` 명세와 100% 일치하는 SQLAlchemy 2.0 ORM 모델 맵핑 완료 (PostGIS POINT 타입 연동 포함).
* **깡통 함수**: 외부 인프라 의존성을 우회하여 5명이 평행 작업을 시작할 수 있도록 `@trace_node` 기반의 `create_supplier_onboarding` 깡통 파이프라인 연동 노드 구현 완료.

## 4. 2주차 이후 구현 로드맵

* **W2~W3**: 데이터 요청 및 제출 현황 타임라인 연동 (`data_request_log` 연동)
* **W3**: 지리 공간 인덱스(GIST)를 활용한 영수 에이전트 전용 PostGIS 헬퍼 함수 개발 지원
* **W4**: `supplier_risk_profiles` 기반 종합 리스크 스코어링 감점식 엔진 구현 및 HITL 인터럽트 연동

## 5/26 한 일 (요약)

Supplier 마스터 CRUD 및 상태머신 뼈대 구축 완료

팀 전체가 복사해서 쓸 이벤트 발행 레퍼런스(SupplierInvited) 구조 완성
수요일 목표인 목록 필터링 고도화 및 RiskProfileUpdated 발행 로직까지 선반영하여 조기 달성

1. 코드 동작 설명

**router.py** (인터폰): 사용자가 "협력사 등록"을 누르면 요청을 받아 service로 넘김. 얇은 계층 원칙에 따라 여기서 직접 DB 커밋 안 함.

**service.py** (지휘관): 14일 마감일 계산 등 비즈니스 로직을 처리. DB 저장이 완벽히 끝난 후(commit)에 알림(이벤트)을 시스템에 방송함. 타 도메인 작업자는 이 파일의 publish 2-인자 방식을 그대로 복사해서 쓰면 됨.

**repository.py** (창고 관리): DB 입출력 전담. 단건 조회 시 협력사 정보와 연관된 공장, 제조 상세 정보 등을 한 번에 묶어 가져와서 성능 저하(N+1) 방지함.

**state_machine.py** (문지기): 협력사 상태가 마음대로 점프하지 못하게 전이 규칙(매트릭스)으로 막음. 상태 변경 시 @trace_node로 감사 로그(Audit) 무조건 남김.

2. 자가검증 4종 결과

**계약 스캔**: 위반 0건 (backend. import, 2-인자 publish, 언더스코어 표기 모두 준수).

**스키마 대조**: schema.sql과 models.py ORM 컬럼 1:1 완벽 일치.

동작 시나리오 (cURL):

Bash
curl -X POST "http://localhost:8000/suppliers" \
-H "Content-Type: application/json" \
-d '{"tenant_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", "company_name": "한양셀 제조(주)", "supplier_type": "manufacturer", "email": "esg@hanyang.demo"}'
동작 흐름 한 줄: POST /suppliers API 호출 → suppliers 테이블 INSERT → DB Commit → SupplierInvitedEvent 발행.

4. 내일 이어서 할 일 (Next Action)

진도를 당겼으므로 내일은 바로 목요일 작업으로 직행함.

Supplier 예외처리 세분화 방어벽 구축 및 CTI 상세(manufacturer, miner 등) 연결 점검 진행 예정.