from pydantic import BaseModel, Field


class ExtractedPlace(BaseModel):
    route_order: int = Field(ge=1)
    original_name: str
    normalized_name: str
    date_text: str | None = None
    sentence: str
    route_role: str
    place_type: str | None = None
    historical_region: str | None = None
    confidence: float = Field(ge=0, le=1)


class PlaceExtraction(BaseModel):
    places: list[ExtractedPlace]


class PlaceUpdate(BaseModel):
    route_order: int | None = None
    original_name: str | None = None
    normalized_name: str | None = None
    date_text: str | None = None
    sentence: str | None = None
    route_role: str | None = None
    place_type: str | None = None
    historical_region: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    selected_lon: float | None = None
    selected_lat: float | None = None
    active: bool | None = None


class PlaceCreate(BaseModel):
    route_order: int
    original_name: str
    normalized_name: str | None = None
    date_text: str | None = None
    sentence: str = ""
    route_role: str = "uncertain"
    place_type: str | None = None
    historical_region: str | None = None
    confidence: float = 1.0
