"""多层缓存 (L1 内存 + L2 Redis)

对语义相同的查询命中缓存，减少重复 LLM/检索开销。
L1 为进程内 LRU，L2 为可选的 Redis 共享缓存。
"""
import threading
from typing import OrderedDict, Any


class MultiTierCache:
    def __init__(self, redis_client: Any = None, ttl_seconds: int = 3600, l1_max_size: int = 1000) -> None:
        self._l1: OrderedDict[str, Any] = OrderedDict()
        self._l1_max_size = l1_max_size
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    @staticmethod
    def _make_key(query:str,tenant_id: str = "default") -> str:
        """生成缓存键"""
        return f"{tenant_id}:{query}"

    def get(self, query: str, tenant_id: str = "default"):
        key = self._make_key(query, tenant_id)
        # 先从 L1 缓存获取
        value = self._l1.get(key)
        if value is not None:
            self._l1.move_to_end(key)  # 更新 LRU 顺序
            self._hits += 1
            return value

        # 如果 L1 缓存未命中，尝试从 L2 缓存获取
        if self._redis is not None:
            value = self._redis.get(key)
            if value is not None:
                # 将 L2 缓存的结果写入 L1 缓存
                self._l1[key] = value
                self._l1.move_to_end(key)  #移到末尾 更新 LRU 顺序
                self._hits += 1
                if len(self._l1) > self._l1_max_size:
                    self._l1.popitem(last=False)  # 移除头部最旧的条目
                return value

        return None

    def set(self, query: str, value: Any, tenant_id: str = "default") -> None:
        key = self._make_key(query, tenant_id)
        # 同时写入 L1 和 L2 缓存
        self._l1[key] = value
        self._l1.move_to_end(key)  # 更新 LRU 顺序
        if len(self._l1) > self._l1_max_size:
            self._l1.popitem(last=False)  # 移除最旧的条目
        if self._redis is not None:
            self._redis.set(key, value, ex=self._ttl)
multi_tier_cache = MultiTierCache()
