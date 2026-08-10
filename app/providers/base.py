from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateResult:
    source: str
    candidate_name: str
    lon: float
    lat: float
    source_id: str | None = None
    admin: str | None = None
    source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Provider:
    name = "base"
    source_weight = 0.5

    async def search(self, name: str, *, year: int | None = None, region: str | None = None,
                     bias: tuple[float, float] | None = None) -> list[CandidateResult]:
        raise NotImplementedError
