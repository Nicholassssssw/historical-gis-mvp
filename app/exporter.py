from sqlalchemy.orm import Session
from .models import Place, Project
from .place_roles import normalize_route_role


def project_geojson(db: Session, project: Project):
    places = (
        db.query(Place)
        .filter(
            Place.project_id == project.id,
            Place.active == True,
            Place.coordinate_selected == True,
        )
        .order_by(Place.route_order)
        .all()
    )

    point_features = []
    route_coords = []
    for p in places:
        if p.selected_lon is None or p.selected_lat is None:
            continue
        props = {
            "feature_kind": "place",
            "place_id": p.id,
            "route_order": p.route_order,
            "name": p.normalized_name,
            "original_name": p.original_name,
            "date_text": p.date_text,
            "route_role": normalize_route_role(p.route_role),
            "place_type": p.place_type,
            "historical_region": p.historical_region,
            "gis_decision": p.gis_decision,
            "record_level": p.record_level,
            "travel_status": p.travel_status,
            "location_status": p.location_status,
            "alias_relation": p.alias_relation,
            "decision_reason": p.decision_reason,
            "previous_route_place": p.previous_route_place,
            "next_route_place": p.next_route_place,
            "adjacency_type": p.adjacency_type,
            "coord_class": p.coord_class,
            "coord_score": p.coord_score,
            "coord_source": p.coord_source,
            "manual_override": p.manual_override,
        }
        point_features.append({
            "type": "Feature",
            "id": p.id,
            "geometry": {"type": "Point", "coordinates": [p.selected_lon, p.selected_lat]},
            "properties": props,
        })
        route_coords.append([p.selected_lon, p.selected_lat])

    features = point_features
    if len(route_coords) >= 2:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": route_coords},
            "properties": {
                "feature_kind": "route",
                "project_id": project.id,
                "name": f"{project.title} - 文本次序暫定路線",
                "note": "按 route_order 直接連接現用坐標；線段不是導航或歷史道路重建。",
            },
        })

    return {
        "type": "FeatureCollection",
        "name": project.title,
        "properties": {
            "project_id": project.id,
            "historical_year": project.historical_year,
            "historical_period": project.historical_period,
            "crs_note": "WGS84 / EPSG:4326",
        },
        "features": features,
    }
