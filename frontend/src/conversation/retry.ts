export class ConversationHttpError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

/** 同一片段/轮次使用原有幂等 ID 重试，业务校验和权限错误不重试。 */
export async function retryConversation<T>(operation: () => Promise<T>, retrying: (attempt: number) => void, cancelled: () => boolean): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try { return await operation(); }
    catch (cause) {
      const temporary = cause instanceof TypeError || (cause instanceof DOMException && cause.name === 'TimeoutError') ||
        (cause instanceof ConversationHttpError && (cause.status === 408 || cause.status === 429 || cause.status >= 500));
      if (!temporary || attempt >= 2 || cancelled()) throw cause;
      retrying(attempt + 1);
      await new Promise(resolve => setTimeout(resolve, 600 * 2 ** attempt));
      if (cancelled()) throw cause;
    }
  }
}
