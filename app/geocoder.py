import asyncio
import json
import math
import os
from difflib import SequenceMatcher
from sqlalchemy import or_
from sqlalchemy.orm import Session
from .models import Candidate, Place, Project
from .place_roles import MAPPED_ROUTE_ROLES
from .providers import default_providers


def _norm(s: str | None) -> str:
    return "".join((s or "").strip().casefold().split())


def name_similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def query_candidates(place: Place, project: Project, bias=None):
    providers = default_providers()
    tasks = [p.search(place.normalized_name or place.original_name,
                      year=project.historical_year,
                      region=place.historical_region,
                      bias=bias) for p in providers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged = []
    for provider, result in zip(providers, results):
        if isinstance(result, Exception):
            continue
        for c in result:
            merged.append((provider, c))
    return merged


def score_and_classify(place: Place, provider_results):
    radius = float(os.getenv("AGREEMENT_RADIUS_KM", "5"))
    confirmed_threshold = float(os.getenv("CONFIRMED_SCORE", "0.86"))
    possible_threshold = float(os.getenv("POSSIBLE_SCORE", "0.60"))

    scored = []
    target_names = [place.original_name, place.normalized_name]
    for provider, c in provider_results:
        ns = max(name_similarity(n, c.candidate_name) for n in target_names if n)
        base = 0.58 * ns + 0.42 * provider.source_weight
        scored.append({"provider": provider, "candidate": c, "name_score": ns,
                       "agreement_count": 0, "score": base})

    # Cross-source agreement: count distinct other sources within radius.
    for i, a in enumerate(scored):
        agreeing_sources = set()
        ca = a["candidate"]
        for j, b in enumerate(scored):
            if i == j or a["provider"].name == b["provider"].name:
                continue
            cb = b["candidate"]
            if haversine_km(ca.lon, ca.lat, cb.lon, cb.lat) <= radius:
                agreeing_sources.add(b["provider"].name)
        a["agreement_count"] = len(agreeing_sources)
        a["score"] = min(1.0, a["score"] + min(0.18, 0.06 * len(agreeing_sources)))

    scored.sort(key=lambda x: x["score"], reverse=True)
    if not scored:
        return [], None, "insufficient", 0.0

    best = scored[0]
    historical_direct = best["provider"].name in {"CHGIS", "DILA", "CBDB"} and best["name_score"] >= 0.82
    cross_agreement = best["agreement_count"] >= 1 and best["score"] >= confirmed_threshold

    if historical_direct or cross_agreement:
        cls = "confirmed"
    elif best["score"] >= possible_threshold:
        cls = "possible"
    else:
        cls = "insufficient"

    return scored, best, cls, float(best["score"])


async def geocode_project(db: Session, project: Project):
    places = db.query(Place).filter(
        Place.project_id == project.id,
        Place.active == True,
        Place.route_role.in_(MAPPED_ROUTE_ROLES),
        or_(Place.gis_decision == None, Place.gis_decision == "retain"),
        or_(Place.record_level == None, Place.record_level == "core"),
    ).order_by(Place.route_order).all()
    previous_bias = None
    summary = []

    for place in places:
        db.query(Candidate).filter(Candidate.place_id == place.id).delete()
        provider_results = await query_candidates(place, project, bias=previous_bias)
        scored, best, cls, best_score = score_and_classify(place, provider_results)

        persisted = []
        best_candidate_row = None
        for item in scored[:30]:
            c = item["candidate"]
            row = Candidate(
                place_id=place.id,
                source=item["provider"].name,
                source_id=c.source_id,
                candidate_name=c.candidate_name[:255],
                lon=c.lon,
                lat=c.lat,
                admin=c.admin,
                source_url=c.source_url,
                name_score=item["name_score"],
                source_weight=item["provider"].source_weight,
                agreement_count=item["agreement_count"],
                total_score=item["score"],
                raw_json=json.dumps(c.raw, ensure_ascii=False, default=str)[:200000],
            )
            db.add(row)
            db.flush()
            persisted.append(row)
            if best is item:
                best_candidate_row = row

        if best_candidate_row:
            place.selected_lon = best_candidate_row.lon
            place.selected_lat = best_candidate_row.lat
            place.coord_source = best_candidate_row.source
            place.coord_score = best_score
            place.coord_class = cls
            if cls in {"confirmed", "possible"}:
                previous_bias = (place.selected_lon, place.selected_lat)
        else:
            place.selected_lon = None
            place.selected_lat = None
            place.coord_source = None
            place.coord_score = 0.0
            place.coord_class = "insufficient"

        db.commit()
        summary.append({
            "place_id": place.id,
            "route_order": place.route_order,
            "name": place.normalized_name,
            "coord_class": place.coord_class,
            "score": place.coord_score,
            "source": place.coord_source,
            "lon": place.selected_lon,
            "lat": place.selected_lat,
            "candidates": [candidate_to_dict(x) for x in persisted[:10]],
        })
    return summary


def candidate_to_dict(c: Candidate):
    return {
        "id": c.id,
        "source": c.source,
        "source_id": c.source_id,
        "candidate_name": c.candidate_name,
        "lon": c.lon,
        "lat": c.lat,
        "admin": c.admin,
        "source_url": c.source_url,
        "name_score": c.name_score,
        "agreement_count": c.agreement_count,
        "score": c.total_score,
    }
