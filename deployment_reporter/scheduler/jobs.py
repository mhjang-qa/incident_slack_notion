import logging
import threading

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, reporter):
        self.reporter = reporter
        self.stop_event = threading.Event()
        self.thread = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        if self.running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="deployment-poller", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.reporter.batch()
            except Exception as exc:
                LOG.error("Scheduler cycle failed: %s", type(exc).__name__)
                self.reporter.repo.log("Scheduler", "Cycle failed; will retry next interval")
            self.stop_event.wait(self.reporter.settings.poll_interval_seconds)
