from enum import StrEnum
from typing import Any, NamedTuple


class ErrorCode(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    REQUEST_CONFLICT = "REQUEST_CONFLICT"
    REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SERVICE_OVERLOADED = "SERVICE_OVERLOADED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    MODEL_BUDGET_EXCEEDED = "MODEL_BUDGET_EXCEEDED"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_UPSTREAM_ERROR = "MODEL_UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorSpec(NamedTuple):
    http_status: int
    retryable: bool
    message: str


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.REQUEST_INVALID: ErrorSpec(400, False, "请求参数不合法"),
    ErrorCode.VALIDATION_FAILED: ErrorSpec(422, False, "请求参数校验失败"),
    ErrorCode.AUTH_INVALID_TOKEN: ErrorSpec(401, False, "认证失败，请重新登录"),
    ErrorCode.AUTH_INVALID_CREDENTIALS: ErrorSpec(401, False, "用户名或密钥错误"),
    ErrorCode.AUTH_FORBIDDEN: ErrorSpec(403, False, "无权限执行该操作"),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorSpec(404, False, "资源不存在"),
    ErrorCode.REQUEST_CONFLICT: ErrorSpec(409, False, "请求状态冲突"),
    ErrorCode.REQUEST_IN_PROGRESS: ErrorSpec(409, True, "重复请求正在处理中，请稍后重试"),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorSpec(413, False, "请求内容超过大小限制"),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorSpec(429, True, "请求过于频繁，请稍后再试"),
    ErrorCode.SERVICE_OVERLOADED: ErrorSpec(503, True, "服务当前繁忙，请稍后重试"),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorSpec(503, True, "服务暂不可用，请稍后重试"),
    ErrorCode.MODEL_BUDGET_EXCEEDED: ErrorSpec(
        429, False, "本次请求已超过模型 token 预算，请缩短问题"
    ),
    ErrorCode.MODEL_AUTH_FAILED: ErrorSpec(502, False, "模型服务鉴权失败，请联系管理员"),
    ErrorCode.MODEL_UPSTREAM_ERROR: ErrorSpec(502, True, "模型服务请求失败，请稍后重试"),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(500, False, "服务内部错误"),
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        spec = ERROR_SPECS[code]
        self.code = code
        self.status_code = spec.http_status
        self.message = message or spec.message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


def error_payload(
    code: ErrorCode,
    message: str | None = None,
    *,
    request_id: str = "",
    details: Any = None,
) -> dict[str, Any]:
    spec = ERROR_SPECS[code]
    error: dict[str, Any] = {
        "code": code.value,
        "message": message or spec.message,
        "retryable": spec.retryable,
        "request_id": request_id,
    }
    if details:
        error["details"] = details
    return {"error": error}
