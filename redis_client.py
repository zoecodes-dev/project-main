import os
from arq import create_pool
from arq.connections import RedisSettings

redis_pool = None

async def get_redis_pool():
    global redis_pool
    if redis_pool is None:
        redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        redis_pool = await create_pool(redis_settings)
    return redis_pool