import os
import asyncio
import httpx
from .base import CandidateResult, Provider


class OSMProvider(Provider):
    name = "OpenStreetMap"
    source_weight = 0.80

    async def search(self, name: str, *, year=None, region=None, bias=None):
        base = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org").rstrip("/")
        ua = os.getenv("NOMINATIM_USER_AGENT", "HistoricalGISResearch/0.1")
        query = " ".join(x for x in [name, region] if x)
        params = {"q": query, "format": "jsonv2", "limit": 6, "addressdetails": 1,
                  "namedetails": 1, "extratags": 1}
        # Public Nominatim policy asks clients to stay at or below ~1 request/second.
        await asyncio.sleep(1.05)
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": ua}) as client:
            r = await client.get(f"{base}/search", params=params)
            if r.status_code >= 400:
                return []
            rows = r.json()
        out = []
        for row in rows:
            try:
                lon, lat = float(row["lon"]), float(row["lat"])
            except Exception:
                continue
            display = row.get("namedetails", {}).get("name") or row.get("display_name") or name
            osm_type, osm_id = row.get("osm_type"), row.get("osm_id")
            url = None
            if osm_type and osm_id:
                kind = {"node": "node", "way": "way", "relation": "relation"}.get(osm_type)
                if kind:
                    url = f"https://www.openstreetmap.org/{kind}/{osm_id}"
            out.append(CandidateResult(
                self.name, display, lon, lat,
                f"{osm_type}:{osm_id}" if osm_id else None,
                row.get("display_name"), url, row
            ))
        return out
