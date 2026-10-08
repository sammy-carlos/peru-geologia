"""Downloads the INGEMMET layers listed in layers.json, for the whole of Peru.

Each layer is saved as it comes from the GEOCATMIN ArcGIS service, in batches of GeoJSON
compressed with gzip, under raw/<layer id>/:

    layer.json        the layer description: fields, geometry type and official symbology
    ids.json          every OBJECTID the service listed, and when
    batch-00001.geojson.gz, ...
    download.json     what was asked, when, how many features came back and which ids did not

The service does not page by offset, so it is read by OBJECTID: first the list of ids, then
the features in batches of ids. A batch that comes back short is asked again in halves, and
an id that still comes back empty is recorded as missing. The download can be stopped and
run again: finished batches are kept.

Usage: python3 scripts/download.py [layer id ...]
"""

import gzip
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
USER_AGENT = "peru-geologia/0.1 (Water Competence Center; one-off download of public INGEMMET layers)"
# A pause between requests, to keep the load on the public server low.
PAUSE_S = 0.4
RETRIES = 5
TIMEOUT_S = 180


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request(url, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
                payload = response.read()
            parsed = json.loads(payload)
            if isinstance(parsed, dict) and "error" in parsed:
                raise RuntimeError(f"service error: {parsed['error']}")
            return payload, parsed
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as error:
            if attempt == RETRIES:
                raise
            wait = 5 * 2 ** (attempt - 1)
            print(f"    retry {attempt}/{RETRIES - 1} in {wait} s ({error})", flush=True)
            time.sleep(wait)


def features_for(query_url, ids, missing):
    """The features of these ids. A short answer, or a batch the server fails to build
    (an error 500, as with very large polygons), is asked again in halves."""
    try:
        _, collection = request(query_url, {
            "objectIds": ",".join(map(str, ids)),
            "outFields": "*",
            "outSR": "4326",
            "returnGeometry": "true",
            "f": "geojson",
        })
    except urllib.error.HTTPError as error:
        if error.code < 500 or len(ids) == 1:
            raise
        middle = len(ids) // 2
        print(f"    the server failed on {len(ids)} ids ({error.code}); asking again in halves", flush=True)
        return features_for(query_url, ids[:middle], missing) + features_for(query_url, ids[middle:], missing)
    time.sleep(PAUSE_S)
    features = collection.get("features", [])
    if len(features) == len(ids):
        return features
    if len(ids) == 1:
        missing.extend(ids)
        return features
    middle = len(ids) // 2
    print(f"    {len(features)} of {len(ids)} came back; asking again in halves", flush=True)
    return features_for(query_url, ids[:middle], missing) + features_for(query_url, ids[middle:], missing)


def download(server, layer):
    folder = RAW / layer["id"]
    folder.mkdir(parents=True, exist_ok=True)
    layer_url = f"{server}/{layer['service']}/MapServer/{layer['layer']}"
    query_url = f"{layer_url}/query"

    description = folder / "layer.json"
    if not description.exists():
        payload, _ = request(f"{layer_url}?f=json")
        description.write_bytes(payload)

    # The list of ids is kept from the first run, so batches keep their numbers on a rerun.
    ids_file = folder / "ids.json"
    if ids_file.exists():
        ids = json.loads(ids_file.read_text())["objectIds"]
    else:
        _, listing = request(query_url, {"where": "1=1", "returnIdsOnly": "true", "f": "json"})
        ids = sorted(listing["objectIds"])
        ids_file.write_text(json.dumps({"retrieved_at": now(), "count": len(ids), "objectIds": ids}))

    size = layer["batch"]
    batches = [ids[start:start + size] for start in range(0, len(ids), size)]
    records, missing, total = [], [], 0
    for number, batch in enumerate(batches, start=1):
        path = folder / f"batch-{number:05d}.geojson.gz"
        if not path.exists():
            batch_missing = []
            features = features_for(query_url, batch, batch_missing)
            # Ids that came back empty travel with their batch, as a GeoJSON foreign member.
            collection = {"type": "FeatureCollection", "features": features, "missing_ids": batch_missing}
            temporary = path.with_suffix(".tmp")
            with gzip.open(temporary, "wt", encoding="utf-8") as output:
                json.dump(collection, output, ensure_ascii=False)
            temporary.rename(path)
        with gzip.open(path, "rb") as stored:
            content = stored.read()
        collection = json.loads(content)
        count = len(collection["features"])
        missing.extend(collection.get("missing_ids", []))
        records.append({"batch": number, "ids": len(batch), "features": count, "sha256": hashlib.sha256(content).hexdigest()})
        total += count
        if number == 1 or number % 10 == 0 or number == len(batches):
            print(f"  {layer['id']:<15} batch {number:>4}/{len(batches)}   {total:>7,} of {len(ids):,} features", flush=True)

    summary = {
        "layer_id": layer["id"],
        "title": layer["title"],
        "source": "INGEMMET · GEOCATMIN",
        "layer_url": layer_url,
        "query": {"outFields": "*", "outSR": "4326", "f": "geojson", "by": "objectIds", "batch": size},
        "ids_listed_at": json.loads(ids_file.read_text())["retrieved_at"],
        "finished_at": now(),
        "ids_listed": len(ids),
        "features_saved": total,
        "missing_ids": sorted(missing),
        "batches": records,
    }
    (folder / "download.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"  {layer['id']:<15} done: {total:,} features, {len(missing)} missing ids\n", flush=True)


def main():
    config = json.loads((ROOT / "layers.json").read_text())
    wanted = set(sys.argv[1:])
    layers = [layer for layer in config["layers"] if not wanted or layer["id"] in wanted]
    unknown = wanted - {layer["id"] for layer in config["layers"]}
    if unknown:
        sys.exit(f"Unknown layers: {', '.join(sorted(unknown))}")
    print(f"Downloading {len(layers)} INGEMMET layers into {RAW}\n", flush=True)
    for layer in layers:
        download(config["server"], layer)
    print("All layers downloaded.")


if __name__ == "__main__":
    main()
