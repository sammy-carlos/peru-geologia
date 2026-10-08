"""Builds one GeoParquet per layer from what scripts/download.py saved in raw/.

Every original INGEMMET attribute is kept, with the name and type the service declares
(the SHAPE field is the geometry). Three columns record where each feature came from:
source, source_layer_url and retrieved_at. Geometries are in EPSG:4326, as downloaded;
the ones the service sent invalid are repaired and flagged in geometry_repaired, so the
change can be traced. Nothing is grouped, renamed or derived.

Writes data/<layer id>.parquet and data/<layer id>.report.json.

Usage: uv run scripts/build_master.py [layer id ...]
"""

import gzip
import json
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
DATA = ROOT / "data"
TYPES = {
    "esriFieldTypeOID": "BIGINT",
    "esriFieldTypeInteger": "INTEGER",
    "esriFieldTypeSmallInteger": "SMALLINT",
    "esriFieldTypeDouble": "DOUBLE",
    "esriFieldTypeSingle": "FLOAT",
    "esriFieldTypeString": "VARCHAR",
    "esriFieldTypeGlobalID": "VARCHAR",
    "esriFieldTypeGUID": "VARCHAR",
}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def build(connection, layer):
    folder = RAW / layer["id"]
    summary = json.loads((folder / "download.json").read_text())
    description = json.loads((folder / "layer.json").read_text())
    if summary["features_saved"] + len(summary["missing_ids"]) != summary["ids_listed"]:
        sys.exit(f"{layer['id']}: the download is incomplete; run scripts/download.py again.")

    # Each attribute is read from the feature's properties with the type the service declares.
    columns = []
    for field in description["fields"]:
        if field["type"] == "esriFieldTypeGeometry":
            continue
        sql_type = TYPES.get(field["type"])
        if sql_type is None:
            sys.exit(f"{layer['id']}: no type for field {field['name']} ({field['type']})")
        path = literal("$." + json.dumps(field["name"]))
        columns.append(f"TRY_CAST(json_extract_string(properties, {path}) AS {sql_type}) AS {quote(field['name'])}")

    # The batches are rewritten one feature per line, which DuckDB reads in small pieces;
    # a whole batch read as one JSON object does not fit in memory for the large layers.
    with tempfile.TemporaryDirectory() as scratch:
        lines = Path(scratch) / "features.geojsonl"
        largest = 0
        with lines.open("w", encoding="utf-8") as output:
            for batch in sorted(folder.glob("batch-*.geojson.gz")):
                with gzip.open(batch, "rt", encoding="utf-8") as stored:
                    for feature in json.load(stored)["features"]:
                        line = json.dumps(feature, ensure_ascii=False, separators=(",", ":"))
                        largest = max(largest, len(line.encode()))
                        output.write(line + "\n")
        # DuckDB reserves this much per reading thread, so it is sized to the largest feature.
        object_size = max(16 * 2**20, 2 * largest)
        connection.execute(f"""
            CREATE OR REPLACE TABLE layer AS
            SELECT *, NOT ST_IsValid(original) AS geometry_repaired
            FROM (
                SELECT {", ".join(columns)},
                       ST_GeomFromGeoJSON(geometry) AS original
                FROM read_json({literal(str(lines))}, format = 'newline_delimited',
                               columns = {{properties: 'JSON', geometry: 'JSON'}}, maximum_object_size = {object_size})
            )
        """)
    output = DATA / f"{layer['id']}.parquet"
    connection.execute(f"""
        COPY (
            SELECT * EXCLUDE (original, geometry_repaired),
                   {literal(summary['source'])} AS source,
                   {literal(summary['layer_url'])} AS source_layer_url,
                   {literal(summary['ids_listed_at'])}::TIMESTAMPTZ AS retrieved_at,
                   geometry_repaired,
                   CASE WHEN geometry_repaired THEN ST_MakeValid(original) ELSE original END AS geometry
            FROM layer
            ORDER BY "OBJECTID"
        ) TO {literal(str(output))} (FORMAT parquet, COMPRESSION zstd)
    """)

    check = connection.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE geometry_repaired), count(*) FILTER (WHERE geometry IS NULL OR ST_IsEmpty(geometry)),
               count(*) FILTER (WHERE NOT ST_IsValid(geometry)), count(DISTINCT "OBJECTID"),
               min(ST_XMin(geometry)), min(ST_YMin(geometry)), max(ST_XMax(geometry)), max(ST_YMax(geometry)),
               list(DISTINCT ST_GeometryType(geometry)::VARCHAR)
        FROM read_parquet({literal(str(output))})
    """).fetchone()
    report = {
        "layer_id": layer["id"],
        "title": layer["title"],
        "rows": check[0],
        "ids_listed": summary["ids_listed"],
        "missing_ids": len(summary["missing_ids"]),
        "geometries_repaired": check[1],
        "geometries_empty": check[2],
        "geometries_still_invalid": check[3],
        "duplicate_objectids": check[0] - check[4],
        "bbox_epsg4326": [round(value, 4) for value in check[5:9]],
        "geometry_types": sorted(check[9]),
        "attributes": [field["name"] for field in description["fields"] if field["type"] != "esriFieldTypeGeometry"],
        "size_mb": round(output.stat().st_size / 1e6, 1),
    }
    (DATA / f"{layer['id']}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"  {layer['id']:<15} {report['rows']:>7,} rows  {report['size_mb']:>6} MB  "
          f"repaired {report['geometries_repaired']}, empty {report['geometries_empty']}, still invalid {report['geometries_still_invalid']}", flush=True)


def main():
    config = json.loads((ROOT / "layers.json").read_text())
    wanted = set(sys.argv[1:])
    layers = [layer for layer in config["layers"] if not wanted or layer["id"] in wanted]
    DATA.mkdir(exist_ok=True)
    # The national polygon layers do not fit in memory: DuckDB works on a scratch database
    # on disk, which it deletes at the end.
    with tempfile.TemporaryDirectory() as scratch:
        connection = duckdb.connect(str(Path(scratch) / "build.duckdb"))
        connection.execute(f"INSTALL spatial; LOAD spatial; SET preserve_insertion_order = false; SET threads = 4; SET temp_directory = {literal(scratch)};")
        build_all(connection, layers)
        connection.close()


def build_all(connection, layers):
    for layer in layers:
        if not (RAW / layer["id"] / "download.json").exists():
            print(f"  {layer['id']:<15} not downloaded yet, skipped")
            continue
        build(connection, layer)


if __name__ == "__main__":
    main()
