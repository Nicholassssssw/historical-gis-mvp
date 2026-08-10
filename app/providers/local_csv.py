import csv
import os
from pathlib import Path
from .base import CandidateResult, Provider


class LocalCSVProvider(Provider):
    """Search a normalized local CSV built from DILA/CBDB/MCGD public downloads."""

    def __init__(self, source: str, env_var: str, source_weight: float):
        self.name = source
        self.source_weight = source_weight
        self.path = Path(os.getenv(env_var, "")) if os.getenv(env_var) else None
        self.rows = None

    def _load(self):
        if self.rows is not None:
            return
        self.rows = []
        if not self.path or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    row["lon"] = float(row.get("lon") or row.get("longitude") or row.get("LONG"))
                    row["lat"] = float(row.get("lat") or row.get("latitude") or row.get("LAT"))
                except Exception:
                    continue
                self.rows.append(row)

    async def search(self, name: str, *, year=None, region=None, bias=None):
        self._load()
        if not self.rows:
            return []
        needle = name.strip().casefold()
        out = []
        for row in self.rows:
            names = [row.get("name", "")] + (row.get("aliases", "").split("|") if row.get("aliases") else [])
            if not any(needle == n.strip().casefold() or needle in n.strip().casefold() for n in names if n):
                continue
            if year is not None:
                try:
                    vf = int(row["valid_from"]) if row.get("valid_from") else None
                    vt = int(row["valid_to"]) if row.get("valid_to") else None
                    if vf is not None and year < vf:
                        continue
                    if vt is not None and year > vt:
                        continue
                except Exception:
                    pass
            out.append(CandidateResult(
                self.name, row.get("name") or name, row["lon"], row["lat"],
                row.get("source_id"), row.get("admin"), row.get("source_url"), row
            ))
            if len(out) >= 10:
                break
        return out
