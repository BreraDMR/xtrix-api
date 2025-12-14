from typing import Generic, TypeVar, Optional
from pydantic import BaseModel

T = TypeVar('T')


class PaginationParams(BaseModel):
    """Pagination query parameters."""
    page: int = 1
    per_page: int = 20
    
    def __init__(self, page: int = 1, per_page: int = 20, **kwargs):
        super().__init__(page=max(1, page), per_page=min(max(1, per_page), 100), **kwargs)
    
    @property
    def offset(self) -> int:
        """Calculate offset for database query."""
        return (self.page - 1) * self.per_page
    
    @property
    def limit(self) -> int:
        """Get limit for database query."""
        return self.per_page


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response model."""
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
        """Create paginated response."""
        pages = (total + per_page - 1) // per_page if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            per_page=per_page,
            pages=pages,
        )









