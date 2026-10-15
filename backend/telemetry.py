"""可选使用量上报；仅在显式配置统计地址且报告生成成功后触发。"""
import logging

import httpx

import config

logger = logging.getLogger("fangtan.telemetry")


def report_usage(session_id: str, user_name: str) -> None:
    """用稳定事件 ID 幂等上报一次“纪要生成成功”。"""
    if not config.AUTH_ENABLED:
        return
    usage_url = config.TEAMFLOW_USAGE_URL.strip()
    if not usage_url:
        return
    user_name = (user_name or "").strip()
    if not user_name:
        logger.warning("skip usage event without user_name sid=%s", session_id)
        return
    payload = {
        "user_name": user_name,
        "event_id": f"fangtan-report-{session_id}",
        "source": "fangtan-agentic",
        "remark": f"访谈纪要生成成功：{session_id}",
        "usage_count": 1,
    }
    if config.TEAMFLOW_PRODUCT_ID:
        try:
            payload["product_id"] = int(config.TEAMFLOW_PRODUCT_ID)
        except ValueError:
            logger.error("invalid TEAMFLOW_PRODUCT_ID")
            return
    else:
        payload["product_name"] = config.TEAMFLOW_PRODUCT_NAME
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(usage_url, json=payload)
            response.raise_for_status()
    except Exception:
        # 埋点失败不应阻断用户取得已经生成的报告。
        logger.exception("usage event failed sid=%s", session_id)
