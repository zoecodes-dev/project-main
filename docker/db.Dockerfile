FROM postgis/postgis:16-3.4

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

# docker 폴더 내의 모든 sql 파일을 초기화 폴더로 복사
COPY docker/*.sql /docker-entrypoint-initdb.d/
