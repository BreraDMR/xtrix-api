"""Typed questionnaire fields.

A template is plain data: a bounded list of typed fields with plain-text labels.
There is no HTML, no regular expressions and no executable validation rules. Published
versions freeze this field list, so answers always keep the wording they were given.
"""
from typing import Annotated, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import InputModel
from app.schemas.datetimes import UtcDatetime
from app.schemas.errors import invalid

MAX_FIELDS = 50
MAX_OPTIONS = 20
MAX_LABEL_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 500
MAX_TEXT_LENGTH = 5000

FieldKey = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="Stable key used in answers; lowercase letters, digits and underscores",
    ),
]
OptionValue = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Stable option value used in answers",
    ),
]
PlainLabel = Annotated[str, Field(min_length=1, max_length=MAX_LABEL_LENGTH)]
PlainDescription = Annotated[str, Field(max_length=MAX_DESCRIPTION_LENGTH)]


def _plain_text(value: str, *, multiline: bool) -> str:
    forbidden = {chr(code) for code in range(32)} | {"\x7f", "<", ">"}
    if multiline:
        forbidden -= {"\n", "\t"}
    if forbidden.intersection(value):
        raise invalid(
            "invalid_text",
            "Use plain text without control characters or HTML tags",
        )
    return value


class QuestionnaireOption(BaseModel):
    # not InputModel: its wildcard before-validator is forbidden on a discriminated field
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: OptionValue
    label: PlainLabel

    @field_validator("label")
    @classmethod
    def label_is_plain(cls, value: str) -> str:
        return _plain_text(value, multiline=False)


class QuestionnaireFieldBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: FieldKey
    label: PlainLabel
    description: Optional[PlainDescription] = None
    required: bool = False

    @field_validator("label")
    @classmethod
    def label_is_plain(cls, value: str) -> str:
        return _plain_text(value, multiline=False)

    @field_validator("description")
    @classmethod
    def description_is_plain(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _plain_text(value, multiline=True)


class QuestionnaireTextField(QuestionnaireFieldBase):
    type: Literal["text"]
    max_length: int = Field(MAX_LABEL_LENGTH, ge=1, le=MAX_LABEL_LENGTH)


class QuestionnaireLongTextField(QuestionnaireFieldBase):
    type: Literal["long_text"]
    max_length: int = Field(2000, ge=1, le=MAX_TEXT_LENGTH)


class QuestionnaireChoiceField(QuestionnaireFieldBase):
    options: list[QuestionnaireOption] = Field(..., min_length=2, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def options_are_unique(self):
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise invalid("duplicate_option_value", "Option values must be unique")
        labels = [option.label.casefold() for option in self.options]
        if len(labels) != len(set(labels)):
            raise invalid("duplicate_option_label", "Option labels must be unique")
        return self


class QuestionnaireSingleChoiceField(QuestionnaireChoiceField):
    type: Literal["single_choice"]


class QuestionnaireMultiChoiceField(QuestionnaireChoiceField):
    type: Literal["multi_choice"]
    max_selected: Optional[int] = Field(None, ge=1, le=MAX_OPTIONS)

    @model_validator(mode="after")
    def max_selected_fits_options(self):
        if self.max_selected is not None and self.max_selected > len(self.options):
            raise invalid(
                "invalid_max_selected",
                "max_selected can't be greater than the number of options",
            )
        return self


class QuestionnaireBooleanField(QuestionnaireFieldBase):
    type: Literal["boolean"]


QuestionnaireField = Annotated[
    Union[
        QuestionnaireTextField,
        QuestionnaireLongTextField,
        QuestionnaireSingleChoiceField,
        QuestionnaireMultiChoiceField,
        QuestionnaireBooleanField,
    ],
    Field(discriminator="type"),
]


def _unique_field_keys(fields: list[QuestionnaireField]) -> list[QuestionnaireField]:
    keys = [field.key for field in fields]
    if len(keys) != len(set(keys)):
        raise invalid("duplicate_field_key", "Field keys must be unique")
    return fields


QuestionnaireFieldList = Annotated[
    list[QuestionnaireField],
    Field(max_length=MAX_FIELDS),
    AfterValidator(_unique_field_keys),
]


class QuestionnaireTemplateBase(InputModel):
    model_config = ConfigDict(extra="forbid")

    name: PlainLabel
    description: Optional[PlainDescription] = None
    fields: QuestionnaireFieldList

    @field_validator("name")
    @classmethod
    def name_is_plain(cls, value: str) -> str:
        return _plain_text(value, multiline=False)

    @field_validator("description")
    @classmethod
    def description_is_plain(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _plain_text(value, multiline=True)


class QuestionnaireTemplateCreate(QuestionnaireTemplateBase):
    pass


class QuestionnaireTemplateUpdate(InputModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[PlainLabel] = None
    description: Optional[PlainDescription] = None
    fields: Optional[QuestionnaireFieldList] = None

    @field_validator("name")
    @classmethod
    def name_is_plain(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _plain_text(value, multiline=False)

    @field_validator("description")
    @classmethod
    def description_is_plain(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _plain_text(value, multiline=True)

    @model_validator(mode="after")
    def changes_are_present(self):
        if not self.model_fields_set:
            raise invalid("empty_update", "Provide at least one change")
        for name in ("name", "fields"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise invalid(f"{name}_required", f"{name} cannot be null")
        return self


class QuestionnaireTemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    fields: QuestionnaireFieldList
    service_id: Optional[int] = None
    published_version: Optional[int] = Field(
        None, description="Latest published version; the draft may differ from it"
    )
    updated_at: UtcDatetime
