from .chgis import CHGISProvider
from .wikidata import WikidataProvider
from .osm import OSMProvider
from .google_places import GooglePlacesProvider


def default_providers():
    return [
        CHGISProvider(),
        WikidataProvider(),
        OSMProvider(),
        GooglePlacesProvider(),
    ]
