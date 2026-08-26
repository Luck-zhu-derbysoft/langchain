from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "enterprise_agent_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "enterprise_agent_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)

LLM_TOKEN_USAGE = Counter(
    "enterprise_agent_llm_tokens_total",
    "Total LLM token usage",
    ["model"],
)

CACHE_OPERATIONS = Counter(
    "enterprise_agent_cache_operations_total",
    "Cache operations",
    ["result"],
)

ACTIVE_REQUESTS = Gauge(
    "enterprise_agent_active_requests",
    "Current active HTTP requests",
)

ALERT_COUNT = Counter(
    "enterprise_agent_alerts_total",
    "Generated alerts",
    ["severity", "alert_type"],
)

ALERT_NOTIFICATION_COUNT = Counter(
    "enterprise_agent_alert_notifications_total",
    "Alert notification attempts",
    ["channel", "status"],
)
