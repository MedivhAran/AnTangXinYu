"""Redis 分布式锁 / 领导选举。

多 Worker 下确保只有 Leader 执行单例后台任务（心跳提醒、KB 同步等）。
利用 Redis SETNX + TTL：抢到锁的 worker 成为 Leader，每次续租；TTL 到期自动故障转移。
"""

from __future__ import annotations

import asyncio
import os

from loguru import logger

from AnTang.services.redis import redis_client


def _worker_id() -> str:
    return os.environ.get("HOSTNAME", str(os.getpid()))


async def try_acquire_leader(key: str, ttl: int = 15, renew_every: int = 5) -> bool:
    """尝试抢锁并成为 Leader。首次 SETNX 抢锁成功后每 `renew_every`s 续租。

    返回 True 表示是当前 Leader（且已进入续租循环）；返回 False 表示已有其他 Leader。
    仅在返回 True 时调用方才应执行单例后台逻辑。

    用法:
        if await try_acquire_leader("antang:reminder:leader"):
            await reminder_heartbeat_loop()  # loop 内续租
        # 非 Leader 退出 / 降级为候补
    """
    w = _worker_id()
    try:
        ok = redis_client.setNx(key, w, ttl)
    except Exception:
        return False
    if not ok:
        return False

    # 抢到锁 — 启动后台续租
    async def _renew() -> None:
        try:
            while True:
                await asyncio.sleep(renew_every)
                try:
                    # 只有持有者才能续租：GET=w 确认为持有者 → EXPIRE
                    current = redis_client.get(key)
                    if current == w.encode() if isinstance(current, bytes) else str(current) == w:
                        redis_client.connection.expire(key, ttl)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass  # shutdown 取消，干净退出

    asyncio.create_task(_renew())
    logger.info(f"[lock] {key} Leader 当选 worker={w}")
    return True


def has_leader(key: str) -> bool:
    try:
        return redis_client.get(key) is not None
    except Exception:
        return False
