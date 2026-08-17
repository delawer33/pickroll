from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness-проба. Единственный эндпоинт вне `/api/v1` и вне auth."""
    return HealthResponse(status="ok")
