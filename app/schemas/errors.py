from pydantic_core import PydanticCustomError


def invalid(code: str, message: str, **params) -> PydanticCustomError:
    # code is what the web app translates, message is just the english fallback.
    # {name} in the message gets filled from params, other braces are left alone
    return PydanticCustomError(code, message, params)


def invalid_choice(allowed: list[str], what: str = "Value") -> PydanticCustomError:
    return invalid("invalid_choice", f"{what} must be one of: {', '.join(allowed)}", allowed=allowed)
