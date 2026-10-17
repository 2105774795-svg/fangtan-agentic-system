"""独立后台处理进程：python worker.py [--once]。

不会导入 FastAPI 或启动网页服务。处理器的外部请求必须各自设置超时；
此进程使用合作取消，不宣称能中止已发送的云端模型请求。
"""
import argparse
import logging
import os
import signal
import socket
import threading
import uuid

import job_queue
import store


logger = logging.getLogger("fangtan.worker")


class WorkerStopping(job_queue.TaskUnavailable):
    pass


class TaskContext:
    def __init__(self, task, *, lease_seconds=60, stop_event=None):
        self.task = task
        self.task_id = task["id"]
        self.lease_owner = task["lease_owner"]
        self.token = self.lease_owner
        self.lease_seconds = lease_seconds
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._heartbeat_stop = threading.Event()
        self._thread = None

    def check_cancelled(self, db=None):
        # 先查询取消状态，避免将用户取消误当成普通租约丢失而漏掉清理。
        current = job_queue.assert_running(self.task_id, self.lease_owner, db)
        if self.stop_event.is_set():
            raise WorkerStopping("处理进程正在安全退出，任务将重新排队")
        return current

    def progress(self, stage="", progress=None):
        self.check_cancelled()
        if not job_queue.progress(self.task_id, self.lease_owner, stage=stage, progress=progress):
            self.check_cancelled()
            raise job_queue.LeaseLost("更新进度时执行租约失效")

    def _heartbeat(self):
        interval = max(0.05, min(20, self.lease_seconds / 3))
        while not self._heartbeat_stop.wait(interval):
            try:
                if not job_queue.heartbeat(self.task_id, self.lease_owner,
                                           lease_seconds=self.lease_seconds):
                    return
            except Exception:
                # 临时数据库忙不等于仍拥有租约；业务提交仍必须 check_cancelled。
                logger.warning("任务心跳暂时失败 task=%s", self.task_id)

    def __enter__(self):
        self._thread = threading.Thread(target=self._heartbeat, daemon=True,
                                        name="task-heartbeat-" + self.task_id[:12])
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._heartbeat_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(5, self.lease_seconds))


def _handlers():
    # 独立模块仅包含业务适配器，不能反向 import app 启动 API 线程。
    import task_handlers
    return (task_handlers.HANDLERS, getattr(task_handlers, "ON_CANCEL", {}),
            getattr(task_handlers, "ON_SUCCESS", {}))


def _finish_cancel(task, on_cancel):
    cleanup = on_cancel.get(task["kind"])
    try:
        if cleanup is not None:
            cleanup(task["payload"])
        job_queue.finish_cancel(task["id"], task["lease_owner"],
                                stage="已取消并清理临时文件" if cleanup else "已取消")
    except Exception:
        # 保留过期租约供其他 worker 重试清理，不把任务重新投入业务处理。
        logger.exception("取消后的材料清理失败 task=%s", task["id"])


def run_once(*, handlers=None, on_cancel=None, on_success=None, worker_id=None,
             lease_seconds=60, stop_event=None):
    """领取并同步执行至多一个任务；无任务返回 False，其余返回 True。"""
    if stop_event is not None and stop_event.is_set():
        return False
    if handlers is None:
        handlers, default_cancel, default_success = _handlers()
        if on_cancel is None:
            on_cancel = default_cancel
        if on_success is None:
            on_success = default_success
    on_cancel = on_cancel or {}
    on_success = on_success or {}
    identity = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    cancelled = job_queue.claim_cancelled(identity, lease_seconds=lease_seconds, kinds=handlers.keys())
    if cancelled is not None:
        _finish_cancel(cancelled, on_cancel)
        return True
    task = job_queue.claim(identity, lease_seconds=lease_seconds, kinds=handlers.keys())
    if task is None:
        return False
    context = TaskContext(task, lease_seconds=lease_seconds, stop_event=stop_event)
    try:
        with context:
            context.check_cancelled()
            result = handlers[task["kind"]](task["payload"], context)
            context.check_cancelled()
            if not job_queue.complete(task["id"], task["lease_owner"], result):
                context.check_cancelled()
                raise job_queue.LeaseLost("完成任务时执行租约失效")
        logger.info("任务完成 task=%s kind=%s", task["id"], task["kind"])
        cleanup = on_success.get(task["kind"])
        if cleanup is not None:
            try:
                cleanup(task["payload"])
            except Exception:
                # 任务结果已提交，材料清理失败不能把成功任务变回失败。
                logger.exception("任务完成后的材料清理失败 task=%s", task["id"])
    except job_queue.TaskCancelled:
        _finish_cancel(task, on_cancel)
        logger.info("任务已取消 task=%s", task["id"])
    except job_queue.LeaseLost:
        # 新 worker 可能已经重新执行，不写失败、不清理它仍在使用的材料。
        logger.warning("丢弃失去租约的任务结果 task=%s", task["id"])
    except WorkerStopping as exc:
        job_queue.release(task["id"], task["lease_owner"], error=str(exc))
        logger.info("退出前已释放任务 task=%s", task["id"])
    except Exception as exc:
        # 不输出 payload（可能包含逐字稿）或云端鉴权信息。
        job_queue.fail(task["id"], task["lease_owner"], str(exc), retryable=getattr(exc, 'retryable', True))
        logger.warning("任务执行失败 task=%s kind=%s error_type=%s",
                       task["id"], task["kind"], type(exc).__name__)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="访谈·体察持久任务处理进程")
    parser.add_argument("--once", action="store_true", help="执行至多一个可领取任务后退出")
    parser.add_argument("--poll-seconds", type=float, default=1, help="空闲轮询间隔，默认 1 秒")
    parser.add_argument("--lease-seconds", type=float, default=60, help="处理租约时长，默认 60 秒")
    parser.add_argument("--interview-only", action="store_true", help="Only process interview preparation tasks")
    parser.add_argument("--worker-id", default="", help="可选的处理进程标识")
    parser.add_argument("--exclude-kind", action="append", default=[], help="排除任务类型，可重复；使用即时对谈进程时可排除 conversation_reply")
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0 or args.lease_seconds <= 0:
        parser.error("轮询间隔与租约时长必须大于 0")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # 独立启动不依赖 API 已经迁移过领域表。
    store.init()
    job_queue.init()
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    from interview_prep import domain as interview_domain
    interview_domain.init()
    handlers, on_cancel, on_success = _handlers()
    if args.interview_only:
        handlers = {key: value for key, value in handlers.items() if key.startswith("interview_")}
    handlers = {kind: handler for kind, handler in handlers.items() if kind not in args.exclude_kind}
    if not handlers:
        parser.error("至少需要保留一种任务类型")
    while not stop_event.is_set():
        worked = run_once(handlers=handlers, on_cancel=on_cancel, on_success=on_success,
                          worker_id=args.worker_id or None,
                          lease_seconds=args.lease_seconds, stop_event=stop_event)
        if args.once:
            break
        if not worked:
            stop_event.wait(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
