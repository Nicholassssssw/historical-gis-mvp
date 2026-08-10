import os
import httpx
from .base import CandidateResult, Provider


class GooglePlacesProvider(Provider):
    name = "Google Places"
    source_weight = 0.78

    async def search(self, name: str, *, year=None, region=None, bias=None):
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            return []
        body = {"textQuery": " ".join(x for x in [name, region] if x), "languageCode": "zh-TW"}
        if bias:
            body["locationBias"] = {"circle": {
                "center": {"latitude": bias[1], "longitude": bias[0]},
                "radius": 50000.0
            }}
        headers = {
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types",
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post("https://places.googleapis.com/v1/places:searchText", headers=headers, json=body)
            if r.status_code >= 400:
                return []
            places = r.json().get("places", [])
        out = []
        for p in places[:8]:
            loc = p.get("location") or {}
            try:
                lon, lat = float(loc["longitude"]), float(loc["latitude"])
            except Exception:
                continue
            display = p.get("displayName")
            if isinstance(display, dict):
                display = display.get("text")
            pid = p.get("id")
            out.append(CandidateResult(
                self.name, display or name, lon, lat, pid,
                p.get("formattedAddress"),
                f"https://www.google.com/maps/search/?api=1&query_place_id={pid}" if pid else None,
                p
            ))
        return out
