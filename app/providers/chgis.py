import os
import re
import httpx
from .base import CandidateResult, Provider


class CHGISProvider(Provider):
    name = "CHGIS"
    source_weight = 0.98

    def __init__(self):
        self.base = os.getenv("CHGIS_BASE_URL", "https://chgis.hudci.org/tgaz").rstrip("/")

    async def search(self, name: str, *, year=None, region=None, bias=None):
        params = {"n": name, "fmt": "json"}
        if year is not None and -222 <= int(year) <= 1911:
            params["yr"] = str(year)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(f"{self.base}/placename", params=params)
            if r.status_code >= 400:
                return []
            try:
                data = r.json()
            except Exception:
                return []
        return self._parse_any(data)

    def _parse_any(self, data):
        out = []
        seen = set()

        def walk(obj):
            if isinstance(obj, dict):
                candidate = self._from_dict(obj)
                if candidate:
                    key = (candidate.source_id, round(candidate.lon, 6), round(candidate.lat, 6))
                    if key not in seen:
                        seen.add(key)
                        out.append(candidate)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)
        walk(data)
        return out[:10]

    def _from_dict(self, d):
        # TGAZ JSON has varied across versions, so handle common coordinate shapes.
        lon = d.get("lon") or d.get("longitude") or d.get("x")
        lat = d.get("lat") or d.get("latitude") or d.get("y")

        if isinstance(d.get("coordinates"), (list, tuple)) and len(d["coordinates"]) >= 2:
            lon, lat = d["coordinates"][0], d["coordinates"][1]
        geom = d.get("geometry")
        if isinstance(geom, dict) and isinstance(geom.get("coordinates"), list) and len(geom["coordinates"]) >= 2:
            lon, lat = geom["coordinates"][0], geom["coordinates"][1]

        if lon is None or lat is None:
            # Last-resort parsing for textual POINT values.
            text = " ".join(str(v) for v in d.values() if isinstance(v, (str, int, float)))
            m = re.search(r"POINT[^\d-]*(-?\d+(?:\.\d+)?)\D+(-?\d+(?:\.\d+)?)", text, re.I)
            if m:
                a, b = map(float, m.groups())
                # CHGIS human-readable form is often N lat E lon.
                lat, lon = (a, b) if abs(a) <= 90 and abs(b) <= 180 else (b, a)

        try:
            lon, lat = float(lon), float(lat)
        except (TypeError, ValueError):
            return None
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None

        cname = (d.get("name") or d.get("placename") or d.get("label") or
                 d.get("name_trad") or d.get("name_chn") or "CHGIS candidate")
        sid = d.get("id") or d.get("uri") or d.get("placename_id") or d.get("tgaz_id")
        admin = d.get("parent") or d.get("part_of") or d.get("admin")
        url = f"{self.base}/placename/{sid}" if sid and str(sid).startswith("hvd_") else None
        return CandidateResult(self.name, str(cname), lon, lat, str(sid) if sid else None,
                               str(admin) if admin else None, url, d)
