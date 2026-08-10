# Optional local historical gazetteer catalogs

The MVP can query CHGIS, Wikidata, OpenStreetMap/Nominatim and Google Places live.
It can also search local CSV catalogs for DILA, CBDB and MCGD. Put the files in this folder or change the paths in `.env`.

Each CSV should contain at least the following normalized columns:

```csv
name,lon,lat,aliases,admin,valid_from,valid_to,source_id,source_url
```

Only `name`, `lon`, and `lat` are mandatory. `aliases` may contain names separated by `|`.

Suggested source preparation:
- DILA Place Authority: download the open place authority dataset and normalize it into this CSV format.
- CBDB: export address/place records from the downloadable SQLite database into this CSV format.
- Modern China Geospatial Database (MCGD): map its name and LAT/LONG fields into this CSV format.

Do not redistribute source datasets from this project unless their individual licenses allow it. Keep source attribution and license metadata in your production database.
