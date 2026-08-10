import httpx
from .base import CandidateResult, Provider


class WikidataProvider(Provider):
    name = "Wikidata"
    source_weight = 0.84
    api = "https://www.wikidata.org/w/api.php"

    async def search(self, name: str, *, year=None, region=None, bias=None):
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "HistoricalGIS-MVP/0.1"}) as client:
            s = await client.get(self.api, params={
                "action": "wbsearchentities", "format": "json", "language": "zh",
                "uselang": "zh", "search": name, "limit": 8, "type": "item"
            })
            if s.status_code >= 400:
                return []
            hits = s.json().get("search", [])
            ids = [x["id"] for x in hits if x.get("id")]
            if not ids:
                return []
            e = await client.get(self.api, params={
                "action": "wbgetentities", "format": "json", "ids": "|".join(ids),
                "props": "claims|labels|descriptions", "languages": "zh|zh-hant|en"
            })
            if e.status_code >= 400:
                return []
            entities = e.json().get("entities", {})

        out = []
        hit_by_id = {x["id"]: x for x in hits}
        for qid in ids:
            ent = entities.get(qid, {})
            claims = ent.get("claims", {}).get("P625", [])
            for claim in claims[:1]:
                try:
                    val = claim["mainsnak"]["datavalue"]["value"]
                    lon, lat = float(val["longitude"]), float(val["latitude"])
                except Exception:
                    continue
                hit = hit_by_id.get(qid, {})
                label = hit.get("label") or ent.get("labels", {}).get("zh", {}).get("value") or qid
                desc = hit.get("description")
                out.append(CandidateResult(
                    self.name, label, lon, lat, qid, desc,
                    f"https://www.wikidata.org/wiki/{qid}", ent
                ))
        return out[:8]
