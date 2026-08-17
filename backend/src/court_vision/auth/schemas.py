from pydantic import BaseModel


class MeResponse(BaseModel):
    clerk_user_id: str
