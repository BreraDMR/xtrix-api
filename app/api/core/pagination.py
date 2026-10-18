from typing import Generic, TypeVar
from pydantic import BaseModel
T = TypeVar('T')
# past this a page or offset is junk, and (page - 1) * per_page would overflow bigint
MAX_PAGE = 100_000
MAX_OFFSET = 10_000_000
class PaginationParams(BaseModel):
    page: int = 1
    per_page: int = 20
    def __init__(self, page: int = 1, per_page: int = 20, **kwargs):
        # page is capped too - a huge page number used to overflow OFFSET (bigint) into a 500
        super().__init__(page=min(max(1, page), MAX_PAGE), per_page=min(max(1, per_page), 100), **kwargs)
    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page
    @property
    def limit(self) -> int:
        return self.per_page
class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    per_page: int
    pages: int
    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        page: int,
        per_page: int,
    ) -> "PaginatedResponse[T]":
        pages = (total + per_page - 1) // per_page if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            per_page=per_page,
            pages=pages,
        )
