"""仅记录固定错误码，不把模型响应、原话或鉴权信息写进日志。"""
import time
import httpx

ERROR_MESSAGES = {
    'model_timeout': 'AI 回应等待超时，您的讲述已保存。',
    'model_network': 'AI 连接暂时中断，您的讲述已保存。',
    'model_busy': 'AI 服务暂时繁忙，您的讲述已保存。',
    'model_auth': 'AI 服务鉴权失败，请检查本机模型配置。',
    'model_request': 'AI 请求未被服务接受，请检查模型配置。',
    'model_incomplete': 'AI 回应未完整生成，您的讲述已保存。',
    'model_response': 'AI 返回格式异常，您的讲述已保存。',
    'reply_invalid': 'AI 回应格式未通过校验，您的讲述已保存。',
    'agent_internal': '对谈处理暂未完成，您的讲述已保存。',
}


class AgentFailure(RuntimeError):
    def __init__(self, code, *, stage='model', retryable=False):
        self.code = code if code in ERROR_MESSAGES else 'agent_internal'
        self.stage = stage
        self.retryable = retryable
        super().__init__('agent:' + self.code)


def classify(cause):
    if isinstance(cause, AgentFailure): return cause
    if isinstance(cause, httpx.TimeoutException): return AgentFailure('model_timeout', retryable=True)
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
        if status in {408, 429} or status >= 500: return AgentFailure('model_busy', retryable=True)
        return AgentFailure('model_auth' if status in {401, 403} else 'model_request')
    if isinstance(cause, httpx.RequestError): return AgentFailure('model_network', retryable=True)
    return AgentFailure('agent_internal', stage='agent')


def diagnostic(db, pid, task_id, code, stage):
    # code/stage 必须由程序决定；不接收异常原文及模型提供的字符串。
    allowed = set(ERROR_MESSAGES) | {'memory_date', 'memory_quote', 'tool_schema', 'tool_invalid', 'tool_context', 'reply_fallback', 'checkpoint_resumed', 'plan_invalid'}
    db.execute('INSERT INTO memoir_agent_diagnostics(task_id,project_id,code,stage,created_at) VALUES(?,?,?,?,?)',
               (task_id, pid, code if code in allowed else 'agent_internal', stage if stage in {'model', 'agent', 'tool', 'reply', 'plan', 'resume'} else 'agent', time.time()))
