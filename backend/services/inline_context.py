"""旧同步纪要入口的上下文：复用业务事务，但不冒充持久队列任务。"""
import store


class SessionContext:
    def __init__(self, session_id):
        self.session_id = session_id

    def check_cancelled(self, db=None):
        if db is None:
            with store.get() as connection:
                return self.check_cancelled(connection)
        row = db.execute("SELECT status FROM sessions WHERE id=?", (self.session_id,)).fetchone()
        if not row or row["status"] == "deleting":
            raise ValueError("访谈已删除或不存在")

    def progress(self, stage="", progress=None):
        # 旧客户端等待本次请求；不写入虚假的后台进度或执行租约。
        self.check_cancelled()
