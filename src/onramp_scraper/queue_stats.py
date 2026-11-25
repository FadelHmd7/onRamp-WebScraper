"""How many crawl jobs are waiting, from RabbitMQ's management API.

Used by the autoscaler (task W8) and the API's health check (task A7).
``ready`` jobs wait for a worker; ``unacked`` jobs are held by workers,
either running or delayed retries waiting for their time.
"""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import httpx

from onramp_scraper.config import Settings


@dataclass(frozen=True)
class QueueDepth:
    ready: int
    unacked: int
    consumers: int

    @property
    def backlog(self) -> int:
        return self.ready + self.unacked


def management_url(settings: Settings) -> tuple[str, str, str, str]:
    """Base URL, vhost and credentials for the management API, from BROKER_URL."""
    broker = urlsplit(settings.broker_url)
    vhost = unquote(broker.path[1:]) if len(broker.path) > 1 else "/"
    return (
        settings.rabbitmq_api_url.rstrip("/"),
        vhost,
        unquote(broker.username or "guest"),
        unquote(broker.password or "guest"),
    )


def queue_depth(
    settings: Settings, queue: str = "crawl", client: httpx.Client | None = None
) -> QueueDepth:
    """Raises ``httpx.HTTPError`` if RabbitMQ cannot be asked."""
    base, vhost, user, password = management_url(settings)
    url = f"{base}/api/queues/{quote(vhost, safe='')}/{quote(queue, safe='')}"
    http = client or httpx.Client(timeout=5.0)
    try:
        response = http.get(url, auth=(user, password))
        response.raise_for_status()
        data = response.json()
    finally:
        if client is None:
            http.close()
    return QueueDepth(
        ready=int(data.get("messages_ready", 0)),
        unacked=int(data.get("messages_unacknowledged", 0)),
        consumers=int(data.get("consumers", 0)),
    )
