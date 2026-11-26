from onramp_scraper.worker.app import app, ping


def test_crash_safety_settings() -> None:
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.worker_concurrency == 1


def test_ping_task_runs_locally() -> None:
    assert ping() == "pong"
