from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteRole = Literal["passed", "mentioned_only", "passed_and_mentioned"]
GISDecision = Literal["retain", "exclude", "review"]
RecordLevel = Literal["core", "route_auxiliary", "excluded", "pending"]
TravelStatus = Literal[
    "arrived", "passed", "stayed", "visited", "viewed_from_afar",
    "direction_or_branch", "other_person", "historical_recall", "mentioned",
    "uncertain",
]
LocationStatus = Literal["locatable", "regional", "relative", "unlocatable", "unchecked"]
AdjacencyType = Literal["explicit_distance", "explicit_direction", "undetermined"]


class ExtractedPlace(BaseModel):
    route_order: int = Field(ge=1)
    original_name: str
    normalized_name: str
    date_text: str | None = None
    sentence: str
    route_role: RouteRole
    place_type: str | None = None
    historical_region: str | None = None
    gis_decision: GISDecision = "review"
    record_level: RecordLevel = "pending"
    travel_status: TravelStatus = "uncertain"
    location_status: LocationStatus = "unchecked"
    alias_relation: str | None = None
    decision_reason: str = ""
    previous_route_place: str | None = None
    next_route_place: str | None = None
    adjacency_type: AdjacencyType = "undetermined"
    confidence: float = Field(ge=0, le=1)


class PlaceExtraction(BaseModel):
    places: list[ExtractedPlace]


class PlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_role: RouteRole | None = None
    historical_region: str | None = None
    selected_lon: float | None = None
    selected_lat: float | None = None


class PlaceBulkRouteRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_role: RouteRole
    place_ids: list[int] | None = None


class PlaceCreate(BaseModel):
    route_order: int
    original_name: str
    normalized_name: str | None = None
    date_text: str | None = None
    sentence: str = ""
    route_role: RouteRole = "passed"
    place_type: str | None = None
    historical_region: str | None = None
    gis_decision: GISDecision = "review"
    record_level: RecordLevel = "pending"
    travel_status: TravelStatus = "uncertain"
    location_status: LocationStatus = "unchecked"
    alias_relation: str | None = None
    decision_reason: str = ""
    previous_route_place: str | None = None
    next_route_place: str | None = None
    adjacency_type: AdjacencyType = "undetermined"
    confidence: float = 1.0
