from fastapi import APIRouter

from court_vision.auth.dependencies import CurrentUserId
from court_vision.auth.schemas import MeResponse

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def me(clerk_user_id: CurrentUserId) -> MeResponse:
    """Идентификатор текущего пользователя: фронту — проверить связку токена с API."""
    return MeResponse(clerk_user_id=clerk_user_id)
