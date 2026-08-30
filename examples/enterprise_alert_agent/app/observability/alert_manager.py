import asyncio
import logging
import queue
import threading
import uuid
from datetime import datetime, timedelta

import httpx

from app.config.settings import settings
from app.observability.alert_types import Alert, AlertSeverity, AlertTypes
from app.observability.prometheus_metrics import (
    ALERT_COUNT,
    ALERT_NOTIFICATION_COUNT,
)

logger = logging.getLogger(__name__)


class AlertManager:
    """管理告警的生成、存储和分发"""

    def __init__(self) -> None:
        self.alerts: dict[str, Alert] = {}  # 存储告警事件
        self.alert_history: list[Alert] = []  # 告警历史记录
        self._max_history_size = 1000  # 最大历史记录数
        self._dispatch_queue: queue.Queue[Alert] = queue.Queue()
        self._stop_event = threading.Event()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_worker, daemon=True, name="AlertDispatchThread"
        )
        self._dispatch_thread.start()

    def create_alert(
        self,
        alert_type: AlertTypes,
        severity: AlertSeverity,
        title: str,
        message: str,
        affected_resource: str,
        context: dict | None = None,
    ) -> Alert:
        alert_id = f"alert_{uuid.uuid4().hex[:12]}"
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            affected_resource=affected_resource,
            context=context or {},
        )
        self.alerts[alert_id] = alert
        self._add_to_history(alert)
        ALERT_COUNT.labels(
            severity=severity.value,
            alert_type=alert_type.value,
        ).inc()
        logger.warning(
            "Alert created: severity=%s, title=%s, resource=%s",
            severity.value,
            title,
            affected_resource,
        )
        # 异步分发告警
        self._dispatch_queue.put(alert)
        return alert

    def _dispatch_worker(self) -> None:
        """告警分发线程"""
        while not self._stop_event.is_set() or not self._dispatch_queue.empty():
            try:
                alert = self._dispatch_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                asyncio.run(self._dispatch_alert(alert))
            except Exception as e:
                logger.error("Failed to dispatch alert: %s", e)
            finally:
                self._dispatch_queue.task_done()

    def close(self, timeout: float = 10.0) -> None:
        """Stop alert dispatch after pending alerts have been processed."""
        self._stop_event.set()
        self._dispatch_queue.join()
        self._dispatch_thread.join(timeout=timeout)

    async def _dispatch_alert(self, alert: Alert) -> None:
        """分发告警到各个渠道"""
        # 根据严重程度和规则决定分发方式
        if alert.severity == AlertSeverity.CRITICAL:
            # 严重级别告警：邮件 + 短信 + 内部通知
            await asyncio.gather(
                self._send_email(alert),
                self._send_sms(alert),
                self._send_internal_notification(alert),
            )
        elif alert.severity == AlertSeverity.WARNING:
            # 警告级别：邮件 + 内部通知
            await asyncio.gather(self._send_email(alert), self._send_internal_notification(alert))
        else:
            # 信息级别：仅内部通知
            await asyncio.gather(self._send_internal_notification(alert))

    async def _send_email(self, alert: Alert):
        """发送邮件告警 (示例实现)"""
        try:
            logger.info("Sending email alert for %s to ops@company.com", alert.alert_id)
            # TODO: 集成邮件服务 (如 SendGrid, AWS SES)
            # from services.email_service import send_email
            # await send_email(
            #     to="ops@company.com",
            #     subject=f"[{alert.severity.value.upper()}] {alert.title}",
            #     body=self._format_alert_email(alert)
            # )
        except Exception as e:
            logger.error("Failed to send email alert: %s", e)

    async def _send_sms(self, alert: Alert):
        """发送短信告警 (示例实现)"""
        try:
            logger.info("Sending SMS alert for %s to +8613800138000", alert.alert_id)
            # TODO: 集成短信服务 (如 Twilio, 阿里云)
            # from services.sms_service import send_sms
            # await send_sms(
            #     phone="+8613800138000",
            #     message=f"[{alert.severity.value.upper()}] {alert.title}: {alert.message[:50]}"
            # )
        except Exception as e:
            logger.error("Failed to send SMS alert: %s", e)

    async def _send_internal_notification(self, alert: Alert) -> None:
        """通过配置的 Webhook 发送内部通知。"""
        if not settings.alert_webhook_url:
            logger.warning("Alert webhook is not configured: alert_id=%s", alert.alert_id)
            ALERT_NOTIFICATION_COUNT.labels(channel="webhook", status="skipped").inc()
            return

        payload = {
            "alert_id": alert.alert_id,
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "title": alert.title,
            "message": alert.message,
            "affected_resource": alert.affected_resource,
            "timestamp": alert.timestamp.isoformat(),
            "context": alert.context,
        }

        try:
            async with httpx.AsyncClient(timeout=settings.alert_webhook_timeout_seconds) as client:
                response = await client.post(settings.alert_webhook_url, json=payload)
                response.raise_for_status()
            ALERT_NOTIFICATION_COUNT.labels(channel="webhook", status="success").inc()
            logger.info("Alert notification sent: alert_id=%s", alert.alert_id)
        except httpx.HTTPError:
            ALERT_NOTIFICATION_COUNT.labels(channel="webhook", status="failure").inc()
            logger.exception("Alert notification failed: alert_id=%s", alert.alert_id)

    @staticmethod
    def _format_alert_email(alert: Alert) -> str:
        """格式化邮件内容"""
        return f"""
        告警标题: {alert.title}
        告警类型: {alert.alert_type.value}
        严重程度: {alert.severity.value}
        受影响资源: {alert.affected_resource}
        消息: {alert.message}
        时间: {alert.timestamp.isoformat()}

        上下文:
        {alert.context}
        """

    def _add_to_history(self, alert: Alert) -> None:
        self.alert_history.append(alert)
        if len(self.alert_history) > self._max_history_size:
            self.alert_history = self.alert_history[-self._max_history_size :]  # 保留最新的历史记录

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """确认告警"""
        alert = self.alerts.get(alert_id)
        if not alert:
            logger.warning("Alert %s not found for acknowledgment", alert_id)
            return False
        alert.acknowledged = True
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.utcnow()
        logger.info("Alert %s acknowledged by %s", alert_id, acknowledged_by)
        return True

    def get_alerts(
        self,
        alert_type: AlertTypes | None = None,
        severity: AlertSeverity | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """获取指定告警"""
        alerts = list(self.alerts.values())
        if alert_type:
            alerts = [alert for alert in alerts if alert.alert_type == alert_type]
        if severity:
            alerts = [alert for alert in alerts if alert.severity == severity]
        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)[:limit]

    def get_alert_history(self, hours: int = 24, limit: int = 1000) -> list[Alert]:
        """获取告警历史记录"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        filtered_alerts = [alert for alert in self.alert_history if alert.timestamp >= cutoff_time]
        return sorted(filtered_alerts, key=lambda a: a.timestamp, reverse=True)[:limit]


alert_manager = AlertManager()
