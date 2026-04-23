# 1. 파이썬 3.11 버전 슬림형(가벼운 버전) 사용
FROM python:3.11-slim

# 2. 컨테이너 내부 작업 폴더 설정
WORKDIR /app

# 3. 필요한 라이브러리 목록 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 내 소스코드 전체를 컨테이너 안으로 복사
COPY . .

# 5. 프로그램 실행 (예: main.py 실행)
CMD ["python", "main.py"]
지나갑니다!