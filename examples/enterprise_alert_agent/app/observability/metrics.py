


from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PerformanceMetrics:
    """性能指标汇总"""
    request_id: str
    total_time_ms: float                # 总耗时
    latencies_ms: list[float] = field(default_factory=list)  # 所有子任务延迟
    token_usage: int = 0                # 消耗的 token 数
    estimated_cost_usd: float = 0.0    # 预计成本（美元）
    cache_hit_count: int = 0            # 缓存命中次数
    cache_miss_count: int = 0           # 缓存未命中次数
    error_count: int = 0                # 错误数
    retry_count: int = 0                # 重试次数
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
    def __init__(self)-> None:
        self.metrics_store: dict[str, PerformanceMetrics] = {}
    def create_metrics(self, request_id: str)-> PerformanceMetrics:
        metrics = PerformanceMetrics(request_id=request_id, total_time_ms=0.0)
        self.metrics_store[request_id] = metrics
        return metrics
    def record_latency(self, request_id: str, latency_ms: float):
            """记录延迟"""
            if request_id in self.metrics_store:
                self.metrics_store[request_id].latencies_ms.append(latency_ms)

    def record_token_usage(self, request_id: str, tokens: int):
        """记录 token 使用"""
        if request_id in self.metrics_store:
            self.metrics_store[request_id].token_usage += tokens
            # 假设 1K token = $0.002 (根据实际模型定价调整)
            self.metrics_store[request_id].estimated_cost_usd += (tokens / 1000) * 0.002

    def record_cache_hit(self, request_id: str):
        """记录缓存命中"""
        if request_id in self.metrics_store:
            self.metrics_store[request_id].cache_hit_count += 1

    def record_cache_miss(self, request_id: str):
        """记录缓存未命中"""
        if request_id in self.metrics_store:
            self.metrics_store[request_id].cache_miss_count += 1

    def record_error(self, request_id: str):
        """记录错误"""
        if request_id in self.metrics_store:
            self.metrics_store[request_id].error_count += 1

    def record_retry(self, request_id: str):
        """记录重试"""
        if request_id in self.metrics_store:
            self.metrics_store[request_id].retry_count += 1

    def get_metrics(self, request_id: str) -> Optional[PerformanceMetrics]:
        """获取指标"""
        return self.metrics_store.get(request_id)
    def get_summary(self, request_id: str) -> Optional[dict]:
        """获取指标汇总"""
        if not self.metrics_store:
            return None
        all_latencies = []
        total_token_usage = 0
        total_cost = 0.0
        total_errors = 0
        for metrics in self.metrics_store.values():
            all_latencies.extend(metrics.latencies_ms)
            total_token_usage += metrics.token_usage
            total_cost += metrics.estimated_cost_usd
            total_errors += metrics.error_count
        if not all_latencies:
            return None
        return {
            "p50_latency_ms": sorted(all_latencies)[int(len(all_latencies) * 0.5) - 1],
            "p95_latency_ms": sorted(all_latencies)[int(len(all_latencies) * 0.95) - 1],
            "p99_latency_ms": sorted(all_latencies)[int(len(all_latencies) * 0.99) - 1],
            "total_token_usage": total_token_usage,
            "total_cost": total_cost,
            "total_errors": total_errors
        }
