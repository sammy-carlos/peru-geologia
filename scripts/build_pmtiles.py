"""Builds dist/peru-geologia.pmtiles, one vector tile layer per layer of layers.json, and
dist/estilos.json with the official INGEMMET symbology of each layer.

The tiles are for display. They carry the INGEMMET attributes except SHAPE.AREA and
SHAPE.LEN (measured by the service in its own units) and the source columns, which go to
the archive's metadata instead; the GeoParquet in data/ keeps everything. Features the
service sends without a geometry are left out, since there is nothing to draw.

Each layer is tiled from its own minimum zoom (layers.json) to the shared maximum, which
the map overzooms past. Polygons are simplified along shared borders, so neighbouring
units keep fitting together, and the smallest are merged into their neighbours where a
tile is too full; lines thin out where they are too dense.

Needs tippecanoe and tile-join (brew install tippecanoe).

Usage: uv run scripts/build_pmtiles.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
DATA = ROOT / "data"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
DROPPED = ["SHAPE.AREA", "SHAPE.LEN", "source", "source_layer_url", "retrieved_at", "geometry_repaired"]
SOURCE = "INGEMMET · GEOCATMIN (geocatmin.ingemmet.gob.pe)"


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def tool(name):
    path = shutil.which(name) or (f"/opt/homebrew/bin/{name}" if Path(f"/opt/homebrew/bin/{name}").exists() else None)
    if not path:
        sys.exit(f"{name} is not installed: brew install tippecanoe")
    return path


def export(connection, layer):
    """The layer's GeoParquet as FlatGeobuf, the fastest input for tippecanoe."""
    parquet = DATA / f"{layer['id']}.parquet"
    columns = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM read_parquet({literal(str(parquet))})").fetchall()]
    dropped = ", ".join(f'"{name}"' for name in DROPPED if name in columns)
    output = BUILD / f"{layer['id']}.fgb"
    connection.execute(f"""
        COPY (
            SELECT * EXCLUDE ({dropped})
            FROM read_parquet({literal(str(parquet))})
            WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)
        ) TO {literal(str(output))} WITH (FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326')
    """)
    return output


def tile(layer, source, maxzoom):
    output = BUILD / f"{layer['id']}.pmtiles"
    shape = (
        ["--detect-shared-borders", "--coalesce-smallest-as-needed"]
        if layer["geometry"] == "polygon"
        else ["--drop-densest-as-needed"]
    )
    subprocess.run([
        tool("tippecanoe"), "--force", "--quiet",
        "--output", str(output),
        "--layer", layer["id"],
        "--minimum-zoom", str(layer["minzoom"]),
        "--maximum-zoom", str(maxzoom),
        *shape,
        "--extend-zooms-if-still-dropping",
        str(source),
    ], check=True)
    return output


def symbology(layer):
    """The layer's official renderer, reduced to what a web map needs."""
    renderer = json.loads((RAW / layer["id"] / "layer.json").read_text())["drawingInfo"]["renderer"]

    def symbol(entry):
        symbol = entry or {}
        outline = symbol.get("outline") or {}
        return {
            "type": symbol.get("type"),
            "style": symbol.get("style"),
            "color": symbol.get("color"),
            "width": symbol.get("width"),
            "outline_color": outline.get("color") if outline.get("style") != "esriSLSNull" else None,
            "outline_width": outline.get("width") if outline.get("style") != "esriSLSNull" else None,
        }

    return {
        "type": renderer.get("type"),
        "fields": [renderer[key] for key in ("field1", "field2", "field3") if renderer.get(key)],
        "delimiter": renderer.get("fieldDelimiter"),
        "default": symbol(renderer.get("defaultSymbol")) | {"label": renderer.get("defaultLabel")},
        "values": [
            {"value": info.get("value"), "label": info.get("label"), **symbol(info.get("symbol"))}
            for info in renderer.get("uniqueValueInfos", [])
        ],
    }


def main():
    config = json.loads((ROOT / "layers.json").read_text())
    BUILD.mkdir(exist_ok=True)
    DIST.mkdir(exist_ok=True)
    connection = duckdb.connect()
    connection.execute("INSTALL spatial; LOAD spatial;")

    parts, styles = [], {}
    for layer in config["layers"]:
        print(f"  {layer['id']:<15} exporting", flush=True)
        source = export(connection, layer)
        print(f"  {layer['id']:<15} tiling z{layer['minzoom']}–z{config['maxzoom']}", flush=True)
        parts.append(tile(layer, source, config["maxzoom"]))
        styles[layer["id"]] = {"title": layer["title"], "geometry": layer["geometry"], "minzoom": layer["minzoom"], **symbology(layer)}

    output = DIST / "peru-geologia.pmtiles"
    print("  joining the layers", flush=True)
    subprocess.run([
        tool("tile-join"), "--force", "--quiet", "--no-tile-size-limit",
        "--output", str(output),
        "--name", "Geología del Perú (INGEMMET)",
        "--attribution", f"© {SOURCE}",
        "--description", "Geología 1:100 000 y 1:50 000, fallas, pliegues y geomorfología de INGEMMET, con sus atributos originales.",
        *map(str, parts),
    ], check=True)
    (DIST / "estilos.json").write_text(json.dumps({"source": SOURCE, "layers": styles}, ensure_ascii=False, indent=1))
    print(f"\n  {output.relative_to(ROOT)}  {output.stat().st_size / 1e6:,.0f} MB")
    print(f"  {(DIST / 'estilos.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
