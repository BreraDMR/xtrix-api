from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.api.core.notification_push import TOKEN_PATTERN


class VisitorPushTokenIn(BaseModel):
    token: str = Field(..., max_length=255, description="Expo push token, ExponentPushToken[...]")
    platform: Literal["ios", "android"]

    @field_validator("token")
    @classmethod
    def _expo_token(cls, value: str) -> str:
        if TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("not an Expo push token")
        return value
