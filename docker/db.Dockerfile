FROM postgis/postgis:16-3.4

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

# build context = backend/ 이므로 docker/init.sql 경로로 복사
COPY docker/init.sql /docker-entrypoint-initdb.d/
