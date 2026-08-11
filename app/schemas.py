from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteRole = Literal["passed", "mentioned_only", "passed_and_mentioned"]


class ExtractedPlace(BaseModel):
    route_order: int = Field(ge=1)
    original_name: str
    normalized_name: str
    date_text: str | None = None
    sentence: str
    route_role: RouteRole
    place_type: str | None = None
    historical_region: str | None = None
    confidence: float = Field(ge=0, le=1)


class PlaceExtraction(BaseModel):
    places: list[ExtractedPlace]


class PlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_role: RouteRole | None = None
    historical_region: str | None = None
    selected_lon: float | None = None
    selected_lat: float | None = None


class PlaceCreate(BaseModel):
    route_order: int
    original_name: str
    normalized_name: str | None = None
    date_text: str | None = None
    sentence: str = ""
    route_role: RouteRole = "passed"
    place_type: str | None = None
    historical_region: str | None = None
    confidence: float = 1.0
