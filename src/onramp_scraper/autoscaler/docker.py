"""Add and remove worker containers through the Docker Engine API.

The autoscaler talks to Docker over its socket (``/var/run/docker.sock``)
with plain HTTP requests, so it needs no Docker client library.

A new worker is a copy of a running one: same image, command, environment,
network and Compose labels, with the next container number. Because the
labels match, ``docker compose ps`` lists it as part of the ``worker``
service, and ``docker compose down`` removes it.

A worker is removed with a normal stop (SIGTERM, then 30 s): Celery finishes
the job in hand first, and anything unfinished is re-queued (``acks_late``).
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
NUMBER_LABEL = "com.docker.compose.container-number"
STOP_TIMEOUT = 30  # seconds for a worker to finish its job, as in docker-compose.yml


@dataclass(frozen=True)
class Worker:
    id: str
    name: str
    number: int


class DockerWorkers:
    def __init__(self, project: str, service: str = "worker", client: httpx.Client | None = None):
        self.project = project
        self.service = service
        self.http = client or httpx.Client(
            transport=httpx.HTTPTransport(uds="/var/run/docker.sock"),
            base_url="http://docker",
            timeout=STOP_TIMEOUT + 15,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def running(self) -> list[Worker]:
        """The service's running containers, lowest number first."""
        labels = [f"{PROJECT_LABEL}={self.project}", f"{SERVICE_LABEL}={self.service}"]
        found = self._call(
            "GET", "/containers/json", params={"filters": json.dumps({"label": labels})}
        )
        workers = [
            Worker(
                id=c["Id"],
                name=c["Names"][0].lstrip("/"),
                number=int(c["Labels"].get(NUMBER_LABEL, "0")),
            )
            for c in found
        ]
        return sorted(workers, key=lambda w: w.number)

    def _taken_numbers(self) -> set[int]:
        """Numbers of every container of the service, stopped ones too (names must be unique)."""
        labels = [f"{PROJECT_LABEL}={self.project}", f"{SERVICE_LABEL}={self.service}"]
        found = self._call(
            "GET",
            "/containers/json",
            params={"all": "true", "filters": json.dumps({"label": labels})},
        )
        return {int(c["Labels"].get(NUMBER_LABEL, "0")) for c in found}

    def add(self, template: Worker) -> Worker:
        """Start one more worker, configured like ``template``."""
        spec = self._call("GET", f"/containers/{template.id}/json")
        taken = self._taken_numbers()
        number = next(n for n in range(1, len(taken) + 2) if n not in taken)
        name = f"{self.project}-{self.service}-{number}"
        config = spec["Config"]
        labels = {**config["Labels"], NUMBER_LABEL: str(number)}
        networks = {
            network: {"Aliases": [self.service, name]}
            for network in spec["NetworkSettings"]["Networks"]
        }
        body = {
            # No Hostname: Docker uses the new container's id, which names the
            # worker in Celery and in its heartbeat.
            "Image": config["Image"],
            "Cmd": config["Cmd"],
            "Entrypoint": config["Entrypoint"],
            "Env": config["Env"],
            "User": config["User"],
            "WorkingDir": config["WorkingDir"],
            "Labels": labels,
            "StopSignal": config.get("StopSignal"),
            "StopTimeout": STOP_TIMEOUT,
            "HostConfig": spec["HostConfig"],
            "NetworkingConfig": {"EndpointsConfig": networks},
        }
        created = self._call("POST", "/containers/create", params={"name": name}, json=body)
        self._call("POST", f"/containers/{created['Id']}/start")
        return Worker(id=created["Id"], name=name, number=number)

    def remove(self, worker: Worker) -> None:
        """Stop gracefully, then delete the container."""
        self._call("POST", f"/containers/{worker.id}/stop", params={"t": STOP_TIMEOUT})
        self._call("DELETE", f"/containers/{worker.id}")

    def scale_to(self, target: int) -> tuple[list[Worker], list[Worker]]:
        """Add or remove workers to reach ``target``. Returns (added, removed).

        The highest-numbered workers are removed first, so the ones Compose
        started keep running.
        """
        current = self.running()
        if not current:
            raise RuntimeError("no running worker to copy; start one with docker compose")
        added: list[Worker] = []
        removed: list[Worker] = []
        for _ in range(target - len(current)):
            added.append(self.add(current[0]))
        for worker in reversed(current[target:] if target < len(current) else []):
            self.remove(worker)
            removed.append(worker)
        return added, removed
