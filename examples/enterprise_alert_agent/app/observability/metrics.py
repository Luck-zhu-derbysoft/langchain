from dataclasses import dataclass, field, replace
from threading import RLock


@dataclass
class PerformanceMetrics:
    """性能指标汇总"""

    request_id: str
    total_time_ms: float  # 总耗时
    latencies_ms: list[float] = field(default_factory=list)  # 所有子任务延迟
    token_usage: int = 0  # 消耗的 token 数
    estimated_cost_usd: float = 0.0  # 预计成本（美元）
    cache_hit_count: int = 0  # 缓存命中次数
    cache_miss_count: int = 0  # 缓存未命中次数
    error_count: int = 0  # 错误数
    retry_count: int = 0  # 重试次数

    def get_cache_hit_rate(self) -> float:
        """计算缓存命中率"""
        total_cache_accesses = self.cache_hit_count + self.cache_miss_count
        if total_cache_accesses == 0:
            return 0.0
        return self.cache_hit_count / total_cache_accesses

    def get_success_rate(self) -> float:
        """计算成功率"""
        total_requests = self.error_count + self.retry_count + 1  # 假设至少有一次请求
        if total_requests == 0:
            return 0.0
        return (total_requests - self.error_count) / total_requests

    def get_p50_latency(self) -> float:
        """计算 P50 延迟"""
        if not self.latencies_ms:
            return 0.0
        sorted_latencies = sorted(self.latencies_ms)
        mid_index = len(sorted_latencies) // 2
        if len(sorted_latencies) % 2 == 0:
            return (sorted_latencies[mid_index - 1] + sorted_latencies[mid_index]) / 2.0
        else:
            return sorted_latencies[mid_index]

    def get_p95_latency(self) -> float:
        """计算 P95 延迟"""
        if not self.latencies_ms:
            return 0.0
        sorted_latencies = sorted(self.latencies_ms)
        index_95 = int(len(sorted_latencies) * 0.95) - 1
        return sorted_latencies[max(0, index_95)]

    def get_p99_latency(self) -> float:
        """计算 P99 延迟"""
        if not self.latencies_ms:
            return 0.0
        sorted_latencies = sorted(self.latencies_ms)
        index_99 = int(len(sorted_latencies) * 0.99) - 1
        return sorted_latencies[max(0, index_99)]


class MetricsCollector:
    """性能指标收集器"""

    def __init__(
        self, max_requests: int = 10000, max_latency_samples_per_request: int = 100
    ) -> None:
        self.metrics_store: dict[str, PerformanceMetrics] = {}
        self.max_requests = max_requests
        self.max_latency_samples_per_request = max_latency_samples_per_request
        self._lock = RLock()  # 用于线程安全的访问

    def create_metrics(self, request_id: str) -> PerformanceMetrics:
        with self._lock:
            if len(self.metrics_store) >= self.max_requests:
                # 超过最大请求数，清理最旧的请求指标
                oldest_request_id = next(iter(self.metrics_store))
                del self.metrics_store[oldest_request_id]
            metrics = PerformanceMetrics(request_id=request_id, total_time_ms=0.0)
            self.metrics_store[request_id] = metrics
            return metrics

    def record_latency(self, request_id: str, latency_ms: float):
        """记录延迟"""
        with self._lock:
            if request_id in self.metrics_store:
                metrics = self.metrics_store[request_id]
                if len(metrics.latencies_ms) >= self.max_latency_samples_per_request:
                    metrics.latencies_ms.pop(0)
                metrics.latencies_ms.append(latency_ms)
                metrics.total_time_ms += latency_ms

    def record_token_usage(self, request_id: str, tokens: int):
        """记录 token 使用"""
        with self._lock:
            if request_id in self.metrics_store:
                self.metrics_store[request_id].token_usage += tokens
                # 假设 1K token = $0.002 (根据实际模型定价调整)
                self.metrics_store[request_id].estimated_cost_usd += (tokens / 1000) * 0.002

    def record_cache_hit(self, request_id: str):
        """记录缓存命中"""
        with self._lock:
            if request_id in self.metrics_store:
                self.metrics_store[request_id].cache_hit_count += 1

    def record_cache_miss(self, request_id: str):
        """记录缓存未命中"""
        with self._lock:
            if request_id in self.metrics_store:
                self.metrics_store[request_id].cache_miss_count += 1

    def record_error(self, request_id: str):
        """记录错误"""
        with self._lock:
            if request_id in self.metrics_store:
                self.metrics_store[request_id].error_count += 1

    def record_retry(self, request_id: str):
        """记录重试"""
        with self._lock:
            if request_id in self.metrics_store:
                self.metrics_store[request_id].retry_count += 1

    def get_metrics(self, request_id: str) -> PerformanceMetrics | None:
        """获取指标"""
        with self._lock:
            metrics = self.metrics_store.get(request_id)
            if metrics is None:
                return None
            return replace(metrics, latencies_ms=list(metrics.latencies_ms))

    def get_summary(self, request_id: str) -> dict | None:
        """获取指标汇总"""
        with self._lock:
            metrics = self.get_metrics(request_id)
            if not metrics:
                return None
            return {
                "request_id": metrics.request_id,
                "total_time_ms": metrics.total_time_ms,
                "p50_latency_ms": metrics.get_p50_latency(),
                "p95_latency_ms": metrics.get_p95_latency(),
                "p99_latency_ms": metrics.get_p99_latency(),
                "total_token_usage": metrics.token_usage,
                "estimated_cost_usd": metrics.estimated_cost_usd,
                "cache_hit_count": metrics.cache_hit_count,
                "cache_miss_count": metrics.cache_miss_count,
                "cache_hit_rate": metrics.get_cache_hit_rate(),
                "error_count": metrics.error_count,
                "retry_count": metrics.retry_count,
                "success_rate": metrics.get_success_rate(),
            }
