from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RouteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    from_query: str = Field(alias="from")
    to_query: str = Field(alias="to")
    mode: Literal["walking", "cycling"] = "walking"

    @field_validator("from_query", "to_query", mode="before")
    @classmethod
    def validate_place(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Enter a starting point and destination.")
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Both places must contain at least three characters.")
        if len(normalized) > 160:
            raise ValueError("Place names are too long.")
        return normalized
