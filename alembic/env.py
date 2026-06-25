"""
alembic/env.py — KIRA 비동기(asyncpg) 마이그레이션 환경.

접속 URL은 컨테이너의 환경변수 DATABASE_URL(postgresql+asyncpg://...@db:5432/..)을 그대로
사용한다. autogenerate는 쓰지 않는다 — PostGIS(geometry)·pgvector(vector)·트리거·CHECK 등
ORM이 완전히 표현하지 못하는 요소가 많아, 마이그레이션은 손으로 op.execute("...DDL...")로
작성한다(팀이 이미 01_schema.sql 생 SQL에 익숙). 따라서 target_metadata=None.
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

# DATABASE_URL(컨테이너 주입) 우선. 없으면 alembic.ini의 더미값.
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 손으로 쓰는 마이그레이션 — autogenerate 미사용.
target_metadata = None


def run_migrations_offline() -> None:
    """오프라인(SQL 출력) 모드."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
