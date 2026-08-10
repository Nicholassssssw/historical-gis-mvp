from .chgis import CHGISProvider
from .wikidata import WikidataProvider
from .osm import OSMProvider
from .google_places import GooglePlacesProvider
from .local_csv import LocalCSVProvider


def default_providers():
    return [
        CHGISProvider(),
        LocalCSVProvider("DILA", "DILA_CSV", 0.95),
        LocalCSVProvider("CBDB", "CBDB_CSV", 0.93),
        LocalCSVProvider("MCGD", "MCGD_CSV", 0.82),
        WikidataProvider(),
        OSMProvider(),
        GooglePlacesProvider(),
    ]
