"""Run real crawl jobs on threads instead of Celery workers.

Each thread plays one worker container: its own fetcher and connections, a
shared queue, real PostgreSQL, Redis and test site. Delayed jobs (retries)
wait on a timer before they enter the queue, like a countdown in Celery.
"""

import queue
import threading
import time
from collections.abc import Callable

from pydantic import HttpUrl
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.config import Settings
from onramp_scraper.sites import SiteConfig, load_sites
from onramp_scraper.worker.pipeline import CrawlContext, Job, JobResult, process
from onramp_scraper.worker.runtime import build_context


def local_site(base: str, **changes: object) -> SiteConfig:
    """The testsite config, pointed at a test site served on ``base``."""
    host = base.split("//")[1].split(":")[0]
    update: dict[str, object] = {
        "base_url": HttpUrl(base + "/"),
        "allowed_hosts": [host],
        "seeds": [HttpUrl(f"{base}/catalog?page=1")],
        "requests_per_second": 1000.0,
    }
    update.update(changes)
    return load_sites()["testsite"].model_copy(update=update)


class LocalCrawl:
    """A queue and N worker threads for crawls of one site."""

    def __init__(
        self,
        engine: Engine,
        redis: Redis,
        site: SiteConfig,
        settings: Settings | None = None,
    ) -> None:
        self.engine = engine
        self.redis = redis
        self.sites = {site.key: site}
        self.settings = settings or Settings()
        self.queue: queue.Queue[Job] = queue.Queue()
        self.outstanding = 0  # jobs queued, waiting on a timer, or running
        self.lock = threading.Condition()
        self.results: list[tuple[Job, JobResult]] = []
        self.errors: list[BaseException] = []
        self.before_job: Callable[[Job], None] | None = None

    @property
    def sessions(self) -> sessionmaker[Session]:
        return sessionmaker(bind=self.engine, expire_on_commit=False)

    def context(self) -> CrawlContext:
        return build_context(self.settings, self.sites, self.engine, self.redis, self.enqueue)

    def enqueue(self, jobs: list[Job], delay: float = 0.0) -> None:
        with self.lock:
            self.outstanding += len(jobs)
        for job in jobs:
            if delay > 0:
                threading.Timer(delay, self.queue.put, [job]).start()
            else:
                self.queue.put(job)

    def _done(self) -> None:
        with self.lock:
            self.outstanding -= 1
            self.lock.notify_all()

    def _worker(self, stop: threading.Event) -> None:
        ctx = self.context()
        while not stop.is_set():
            try:
                job = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if self.before_job is not None:
                    self.before_job(job)
                result = process(job, ctx)
                with self.lock:
                    self.results.append((job, result))
            except BaseException as exc:
                self.errors.append(exc)
            finally:
                self._done()

    def run(self, workers: int = 4, timeout: float = 120.0) -> float:
        """Process jobs until none are left. Returns the elapsed seconds."""
        stop = threading.Event()
        threads = [threading.Thread(target=self._worker, args=(stop,)) for _ in range(workers)]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        try:
            with self.lock:
                finished = self.lock.wait_for(lambda: self.outstanding == 0, timeout=timeout)
        finally:
            stop.set()
            for thread in threads:
                thread.join()
        assert finished, f"jobs still outstanding after {timeout}s: {self.outstanding}"
        if self.errors:
            raise self.errors[0]
        return time.perf_counter() - started
