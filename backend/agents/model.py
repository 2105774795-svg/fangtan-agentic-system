"""Kimi 工具调用适配：完整回传工具结果，支持多工具及次数预算。"""
import httpx

import config
from .errors import AgentFailure, classify


def complete(messages, tools):
    if not config.KIMI_API_KEY:
        raise AgentFailure('model_auth')
    payload = {"model": config.KIMI_MODEL, "messages": messages, "tools": tools,
               "tool_choice": {"type": "function", "function": {"name": "respond"}} if len(tools) == 1 and tools[0]['function']['name'] == 'respond' else "auto"}
    if config.KIMI_MODEL.startswith("kimi-k3"):
        payload["reasoning_effort"] = "low"
        payload['max_completion_tokens'] = 4096
    else:
        payload['max_tokens'] = 1600
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False) as client:
            response = client.post(config.KIMI_BASE_URL.rstrip("/") + "/chat/completions",
                                   json=payload, headers={"Authorization": "Bearer " + config.KIMI_API_KEY})
            response.raise_for_status()
            if len(response.content) > 100000: raise AgentFailure('model_response')
            data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length': raise AgentFailure('model_incomplete')
        if choice.get('finish_reason') == 'content_filter': raise AgentFailure('model_request')
        if choice.get('finish_reason') not in {'stop', 'tool_calls'}: raise AgentFailure('model_response')
        message = choice['message']
        if not isinstance(message, dict) or message.get('role') != 'assistant': raise AgentFailure('model_response')
        # K3 需要原样回传 reasoning_content；只在本次运行内存中保留，不进入持久检查点和前端。
        return dict(message)
    except (httpx.HTTPError, AgentFailure) as cause:
        raise classify(cause) from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise AgentFailure('model_response') from None
