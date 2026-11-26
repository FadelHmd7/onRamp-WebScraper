"""Response shapes shared by the routes. They are also the OpenAPI schemas."""

from pydantic import BaseModel


class Page[T](BaseModel):
    """One page of a list: limit/offset with the total, so a UI can show "21-40 of 615"."""

    items: list[T]
    total: int
    limit: int
    offset: int
