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
    * 공급망 중간에 끼어있는 중개상이 상위 공급망을 투명하게 공개하지 않을 경우(`trader_disclosure_obligation.disclosure_completeness < 75%`), 차윤 에이전트는 해당 라인을 위험으로 플래그/차단해야 합니다.
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