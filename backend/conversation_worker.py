"""Dedicated interactive reply lane; archives/imports stay on the main worker."""
import logging
import signal
import threading
import store
import job_queue
import worker
import conversation_engine


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store.init(); job_queue.init(); conversation_engine.init()
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    handlers = {'conversation_reply': conversation_engine.reply_job}
    while not stopping.is_set():
        if not worker.run_once(handlers=handlers, stop_event=stopping):
            stopping.wait(.25)


if __name__ == '__main__':
    main()
