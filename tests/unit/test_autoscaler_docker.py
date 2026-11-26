"""The autoscaler's Docker and RabbitMQ calls, against fake HTTP servers."""

import json
from typing import Any

import httpx
import pytest

from onramp_scraper.autoscaler.docker import NUMBER_LABEL, DockerWorkers
from onramp_scraper.config import Settings
from onramp_scraper.queue_stats import management_url, queue_depth

PROJECT = "onramp-scraper"


class FakeDocker:
    """Just enough of the Docker Engine API: list, inspect, create, start, stop, delete."""

    def __init__(self, numbers: list[int]) -> None:
        self.containers: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []
        for number in numbers:
            self._add(f"id{number}", number, running=True)

    def _add(self, cid: str, number: int, running: bool) -> None:
        self.containers[cid] = {
            "Id": cid,
            "Names": [f"/{PROJECT}-worker-{number}"],
            "running": running,
            "Labels": {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": "worker",
                NUMBER_LABEL: str(number),
                "com.docker.compose.config-hash": "abc",
            },
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if path == "/containers/json":
            everything = request.url.params.get("all") == "true"
            listed = [c for c in self.containers.values() if everything or c["running"]]
            return httpx.Response(200, json=listed)
        if method == "GET" and path.endswith("/json"):
            container = self.containers[path.split("/")[2]]
            return httpx.Response(
                200,
                json={
                    "Config": {
                        "Image": "onramp-scraper-worker",
                        "Cmd": ["celery", "worker"],
                        "Entrypoint": None,
                        "Env": ["BROKER_URL=amqp://x"],
                        "User": "app",
                        "WorkingDir": "/app",
                        "Hostname": container["Id"],
                        "Labels": container["Labels"],
                    },
                    "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                    "NetworkSettings": {"Networks": {"onramp-scraper_default": {}}},
                },
            )
        if path == "/containers/create":
            body = json.loads(request.content)
            self.created.append({"name": request.url.params["name"], **body})
            number = int(body["Labels"][NUMBER_LABEL])
            self._add(f"new{number}", number, running=False)
            return httpx.Response(201, json={"Id": f"new{number}"})
        cid = path.split("/")[2]
        if path.endswith("/start"):
            self.containers[cid]["running"] = True
            return httpx.Response(204)
        if path.endswith("/stop"):
            assert request.url.params["t"] == "30"  # graceful: the job in hand can finish
            self.containers[cid]["running"] = False
            return httpx.Response(204)
        if method == "DELETE":
            del self.containers[cid]
            return httpx.Response(204)
        return httpx.Response(404)


def workers_with(fake: FakeDocker) -> DockerWorkers:
    client = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://docker")
    return DockerWorkers(PROJECT, client=client)


def test_running_workers_in_number_order() -> None:
    fake = FakeDocker([2, 1])
    assert [w.number for w in workers_with(fake).running()] == [1, 2]


def test_a_new_worker_is_a_copy_with_the_next_free_number() -> None:
    fake = FakeDocker([1, 3])
    added, removed = workers_with(fake).scale_to(4)
    assert removed == []
    assert [w.name for w in added] == [f"{PROJECT}-worker-2", f"{PROJECT}-worker-4"]
    first = fake.created[0]
    assert first["Image"] == "onramp-scraper-worker"
    assert first["Env"] == ["BROKER_URL=amqp://x"]
    assert first["Labels"][NUMBER_LABEL] == "2"  # docker compose ps sees it as worker #2
    assert first["Labels"]["com.docker.compose.config-hash"] == "abc"  # compose keeps it
    assert "Hostname" not in first  # its own id names it in Celery and the heartbeat
    aliases = first["NetworkingConfig"]["EndpointsConfig"]["onramp-scraper_default"]["Aliases"]
    assert aliases == ["worker", f"{PROJECT}-worker-2"]
    assert len(workers_with(fake).running()) == 4


def test_scaling_down_stops_the_highest_numbers_gracefully() -> None:
    fake = FakeDocker([1, 2, 3, 4])
    added, removed = workers_with(fake).scale_to(2)
    assert added == []
    assert [w.number for w in removed] == [4, 3]
    assert [w.number for w in workers_with(fake).running()] == [1, 2]
    assert ("POST", "/containers/id4/stop") in fake.calls
    assert ("DELETE", "/containers/id4") in fake.calls


def test_nothing_to_copy_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="no running worker"):
        workers_with(FakeDocker([])).scale_to(2)


# ---------------------------------------------------------------- RabbitMQ


def test_queue_depth_from_the_management_api() -> None:
    seen: list[httpx.Request] = []

    def rabbit(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"messages_ready": 40, "messages_unacknowledged": 4, "consumers": 4}
        )

    settings = Settings(broker_url="amqp://scraper:s%40cret@rabbitmq:5672//")
    client = httpx.Client(transport=httpx.MockTransport(rabbit))
    depth = queue_depth(settings, client=client)
    assert (depth.ready, depth.unacked, depth.consumers, depth.backlog) == (40, 4, 4, 44)
    assert str(seen[0].url) == "http://rabbitmq:15672/api/queues/%2F/crawl"  # vhost "/"
    assert seen[0].headers["authorization"].startswith("Basic ")


def test_management_credentials_come_from_the_broker_url() -> None:
    settings = Settings(
        broker_url="amqp://user:p%40ss@rabbitmq:5672/crawls", rabbitmq_api_url="http://mq:15672/"
    )
    assert management_url(settings) == ("http://mq:15672", "crawls", "user", "p@ss")
