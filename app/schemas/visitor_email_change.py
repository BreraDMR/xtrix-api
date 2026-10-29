"""Security proofs never accept an account or replacement contact from the body."""
from pydantic import ConfigDict, Field, SecretStr

from app.schemas.base import InputModel
from app.schemas.visitor_account import VisitorRegistrationRequestIn


class VisitorEmailChangeConfirmIn(InputModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=1, max_length=128, repr=False)


class VisitorEmailChangeRequestIn(VisitorRegistrationRequestIn):
    pass
