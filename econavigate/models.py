from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CurrentLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str = Field(default="Your location", min_length=1, max_length=80)


class RouteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    from_query: str | CurrentLocation = Field(alias="from")
    to_query: str = Field(alias="to")
    mode: Literal["walking", "cycling"] = "walking"

    @field_validator("from_query", mode="before")
    @classmethod
    def validate_origin(cls, value: object) -> object:
        if isinstance(value, dict):
            return value
        return cls.validate_place(value)

    @field_validator("to_query", mode="before")
    @classmethod
    def validate_destination(cls, value: object) -> str:
        return cls.validate_place(value)

    @staticmethod
    def validate_place(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Enter a starting point and destination.")
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Both places must contain at least three characters.")
        if len(normalized) > 160:
            raise ValueError("Place names are too long.")
        return normalized
