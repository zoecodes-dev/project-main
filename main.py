from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, engine
import models

# 앱 시작 시 테이블 자동 생성
# models.py에 정의해둔 클래스들을 보고 DB에 실제 테이블을 만들어줌
models.Base.metadata.create_all(bind=engine)

app = FastAPI()


@app.get("/")
def root():
    return {"message": "서버 정상 작동 중"}


# 협력사 전체 조회
# Depends(get_db)를 쓰면 API가 호출될 때마다 DB 세션을 열고 다 쓰면 닫아줌
@app.get("/suppliers")
def get_suppliers(db: Session = Depends(get_db)):
    suppliers = db.query(models.Supplier).all()
    return suppliers


# 협력사 추가
@app.post("/suppliers")
def create_supplier(name: str, country: str, db: Session = Depends(get_db)):
    supplier = models.Supplier(name=name, country=country)
    db.add(supplier) # 1. DB 세션에 임시로 추가
    db.commit()      # 2. 실제 DB에 영구 저장 (Commit)
    db.refresh(supplier) # 3. DB에서 방금 생성된 id 등 최신 상태를 다시 불러오기
    return supplier


# 협력사 단건 조회
@app.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    supplier = db.query(models.Supplier).filter(
        models.Supplier.id == supplier_id
    ).first() # .all() 대신 .first()를 써서 조건에 맞는 첫 번째 데이터만 가져옴
    
    if not supplier:
        raise HTTPException(status_code=404, detail="협력사를 찾을 수 없습니다")
    return supplier