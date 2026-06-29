"""
domains/submission/masterform.py  (담당: 팀원 E 차윤)

마스터폼 섹션 4~6 write 함수는 스키마 축소(2026)로 모두 제거되었다.
  - 섹션 4 지분·FEOC (supplier_trader_details)        → 테이블 삭제로 제거
  - 섹션 5 인권·실사·교육 (human_rights/accidents/training) → 테이블 삭제로 제거
  - 섹션 6 EoL·인증서 (supplier_certifications)        → 테이블 삭제로 제거

현재 마스터폼은 섹션 0~1(회사·공장·PIC·탄소발자국)만 저장하며,
해당 write는 B(supplier/repository.py)가 담당한다. 이 모듈에 남은 write 함수는 없다.
"""
