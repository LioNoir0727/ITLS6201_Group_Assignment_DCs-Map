#!/usr/bin/env python3
"""
Prepare a web-friendly Greater Sydney major roads layer.

Input:
  /Users/lionoir/Downloads/new-south-wales-260430-free.gpkg.zip

Output:
  data/major_roads.geojson

The preferred path uses geopandas, as requested. A small stdlib fallback is
included so this script can still run in lightweight Python environments that
do not have geopandas/fiona/pyogrio installed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import sys
import zipfile
from collections import Counter
from pathlib import Path


ZIP_PATH = Path("/Users/lionoir/Downloads/new-south-wales-260430-free.gpkg.zip")
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "data" / "major_roads.geojson"
WORK_DIR = SCRIPT_DIR / "_major_roads_work"

ROADS_LAYER_NAME = "gis_osm_roads_free_1"
BBOX = {
    "min_lon": 150.45,
    "max_lon": 151.45,
    "min_lat": -34.25,
    "max_lat": -33.35,
}
MAJOR_FCLASSES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
}
USEFUL_FIELDS = [
    "osm_id",
    "code",
    "fclass",
    "name",
    "ref",
    "oneway",
    "maxspeed",
    "layer",
    "bridge",
    "tunnel",
]
SIMPLIFY_TOLERANCE = 0.00005


def unzip_gpkg(zip_path: Path, extract_dir: Path) -> Path:
    """Unzip the GeoPackage archive and return the contained .gpkg path."""
    if not zip_path.exists():
        raise FileNotFoundError(f"Input zip was not found: {zip_path}")

    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        gpkg_names = [name for name in archive.namelist() if name.lower().endswith(".gpkg")]
        if not gpkg_names:
            raise FileNotFoundError("No .gpkg file was found inside the zip archive.")

        gpkg_name = gpkg_names[0]
        gpkg_path = extract_dir / Path(gpkg_name).name

        if not gpkg_path.exists() or gpkg_path.stat().st_size == 0:
            print(f"Unzipping {gpkg_name}...")
            with archive.open(gpkg_name) as source, gpkg_path.open("wb") as target:
                while True:
                    chunk = source.read(1024 * 1024 * 16)
                    if not chunk:
                        break
                    target.write(chunk)
        else:
            print(f"Using existing extracted GeoPackage: {gpkg_path}")

    return gpkg_path


def list_layers_sqlite(gpkg_path: Path) -> list[str]:
    """List feature layers from GeoPackage metadata using sqlite."""
    with sqlite3.connect(gpkg_path) as connection:
        rows = connection.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features' ORDER BY table_name"
        ).fetchall()
    return [row[0] for row in rows]


def choose_roads_layer(layers: list[str]) -> str:
    """Choose the roads layer, preferring the expected Geofabrik name."""
    if ROADS_LAYER_NAME in layers:
        return ROADS_LAYER_NAME

    road_like_layers = [layer for layer in layers if "road" in layer.lower()]
    if road_like_layers:
        return road_like_layers[0]

    raise ValueError("Could not identify a roads layer in the GeoPackage.")


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024


def road_group_for_fclass(fclass: str) -> str:
    if fclass in {"motorway", "motorway_link"}:
        return "Motorway"
    if fclass in {"trunk", "trunk_link"}:
        return "Trunk road"
    return "Primary arterial"


def display_name(row: dict) -> str:
    for key in ("ref", "name", "fclass"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "Major road"


def print_counts(
    original_count: int,
    features: list[dict],
    output_path: Path,
) -> None:
    print(f"Original road feature count: {original_count:,}")
    print(f"Filtered major road feature count: {len(features):,}")

    fclass_counts = Counter(feature["properties"].get("fclass", "") for feature in features)
    display_name_counts = Counter(feature["properties"].get("display_name", "") for feature in features)

    print("\nValue counts for fclass:")
    for key, value in fclass_counts.most_common():
        print(f"  {key}: {value:,}")

    print("\nValue counts for display_name:")
    for key, value in display_name_counts.most_common():
        print(f"  {key}: {value:,}")

    print(f"\nOutput file: {output_path}")
    print(f"Output file size: {file_size_mb(output_path):.2f} MB")


def run_with_geopandas(gpkg_path: Path, layers: list[str], roads_layer: str) -> bool:
    """Use geopandas when available. Return True if this path succeeds."""
    try:
        import geopandas as gpd
    except ModuleNotFoundError:
        print("geopandas is not installed in this Python environment; using sqlite fallback.")
        return False

    print("\nGeoPackage layers:")
    for layer in layers:
        print(f"  - {layer}")

    print(f"\nReading roads layer: {roads_layer}")
    roads = gpd.read_file(gpkg_path, layer=roads_layer)
    original_count = len(roads)

    print("\nColumn names:")
    print(list(roads.columns))

    print("\nFirst 10 rows:")
    print(roads.head(10))

    if roads.crs is None:
        print("\nWarning: input roads layer has no CRS. Assuming EPSG:4326.")
        roads = roads.set_crs("EPSG:4326")
    else:
        roads = roads.to_crs("EPSG:4326")

    bbox_filtered = roads.cx[
        BBOX["min_lon"] : BBOX["max_lon"],
        BBOX["min_lat"] : BBOX["max_lat"],
    ]
    major = bbox_filtered[bbox_filtered["fclass"].isin(MAJOR_FCLASSES)].copy()

    fields = [field for field in USEFUL_FIELDS if field in major.columns]
    major = major[fields + ["geometry"]]
    major["road_group"] = major["fclass"].apply(road_group_for_fclass)
    major["display_name"] = major.apply(lambda row: display_name(row), axis=1)
    major["geometry"] = major.geometry.simplify(
        SIMPLIFY_TOLERANCE,
        preserve_topology=True,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    major.to_file(OUTPUT_PATH, driver="GeoJSON")

    features = json.loads(OUTPUT_PATH.read_text()).get("features", [])
    print_counts(original_count, features, OUTPUT_PATH)
    return True


def table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [row[1] for row in rows]


def geometry_column(connection: sqlite3.Connection, table_name: str) -> str:
    row = connection.execute(
        "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
        (table_name,),
    ).fetchone()
    if not row:
        raise ValueError(f"No geometry column was found for layer: {table_name}")
    return row[0]


def print_sqlite_preview(connection: sqlite3.Connection, table_name: str, geom_column: str) -> None:
    columns = table_columns(connection, table_name)

    print("\nColumn names:")
    print(columns)

    preview_columns = [
        f'"{column}"' if column != geom_column else f"'<geometry blob>' AS {geom_column}"
        for column in columns
    ]
    preview_sql = f'SELECT {", ".join(preview_columns)} FROM "{table_name}" LIMIT 10'
    rows = connection.execute(preview_sql).fetchall()

    print("\nFirst 10 rows:")
    for row in rows:
        print(dict(zip(columns, row)))


def rtree_table_name(connection: sqlite3.Connection, table_name: str, geom_column: str) -> str | None:
    candidate = f"rtree_{table_name}_{geom_column}"
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (candidate,),
    ).fetchone()
    return candidate if row else None


def gpkg_wkb_offset(blob: bytes) -> int:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise ValueError("Geometry blob is not a GeoPackage geometry.")

    flags = blob[3]
    envelope_code = (flags >> 1) & 0b111
    envelope_lengths = {
        0: 0,
        1: 32,
        2: 48,
        3: 48,
        4: 64,
    }
    return 8 + envelope_lengths.get(envelope_code, 0)


def wkb_layout(type_code: int) -> tuple[int, int]:
    """Return base geometry type and coordinate dimension for ISO WKB type codes."""
    if type_code >= 3000:
        return type_code - 3000, 4
    if type_code >= 2000:
        return type_code - 2000, 3
    if type_code >= 1000:
        return type_code - 1000, 3
    return type_code, 2


def parse_wkb_geometry(data: bytes, offset: int = 0) -> tuple[dict | None, int]:
    endian_byte = data[offset]
    endian = "<" if endian_byte == 1 else ">"
    offset += 1

    type_code = struct.unpack_from(f"{endian}I", data, offset)[0]
    offset += 4
    base_type, dimensions = wkb_layout(type_code)

    if base_type == 2:  # LineString
        point_count = struct.unpack_from(f"{endian}I", data, offset)[0]
        offset += 4
        coordinates = []
        for _ in range(point_count):
            values = struct.unpack_from(f"{endian}{dimensions}d", data, offset)
            offset += 8 * dimensions
            coordinates.append([round(values[0], 7), round(values[1], 7)])
        return {"type": "LineString", "coordinates": coordinates}, offset

    if base_type == 5:  # MultiLineString
        line_count = struct.unpack_from(f"{endian}I", data, offset)[0]
        offset += 4
        lines = []
        for _ in range(line_count):
            geometry, offset = parse_wkb_geometry(data, offset)
            if geometry and geometry["type"] == "LineString":
                lines.append(geometry["coordinates"])
            elif geometry and geometry["type"] == "MultiLineString":
                lines.extend(geometry["coordinates"])
        return {"type": "MultiLineString", "coordinates": lines}, offset

    if base_type == 7:  # GeometryCollection
        geometry_count = struct.unpack_from(f"{endian}I", data, offset)[0]
        offset += 4
        lines = []
        for _ in range(geometry_count):
            geometry, offset = parse_wkb_geometry(data, offset)
            if geometry and geometry["type"] == "LineString":
                lines.append(geometry["coordinates"])
            elif geometry and geometry["type"] == "MultiLineString":
                lines.extend(geometry["coordinates"])
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}, offset
        if lines:
            return {"type": "MultiLineString", "coordinates": lines}, offset
        return None, offset

    return None, offset


def parse_gpkg_geometry(blob: bytes) -> dict | None:
    if blob is None:
        return None
    data = bytes(blob)
    offset = gpkg_wkb_offset(data)
    geometry, _ = parse_wkb_geometry(data, offset)
    return geometry


def perpendicular_distance(point: list[float], start: list[float], end: list[float]) -> float:
    if start == end:
        return math.dist(point, start)

    x, y = point
    x1, y1 = start
    x2, y2 = end
    numerator = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
    denominator = math.hypot(y2 - y1, x2 - x1)
    return numerator / denominator


def simplify_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) <= 2:
        return points

    max_distance = 0.0
    index = 0
    for point_index in range(1, len(points) - 1):
        distance = perpendicular_distance(points[point_index], points[0], points[-1])
        if distance > max_distance:
            index = point_index
            max_distance = distance

    if max_distance > tolerance:
        left = simplify_line(points[: index + 1], tolerance)
        right = simplify_line(points[index:], tolerance)
        return left[:-1] + right

    return [points[0], points[-1]]


def simplify_geometry(geometry: dict, tolerance: float) -> dict:
    if geometry["type"] == "LineString":
        return {
            "type": "LineString",
            "coordinates": simplify_line(geometry["coordinates"], tolerance),
        }

    if geometry["type"] == "MultiLineString":
        return {
            "type": "MultiLineString",
            "coordinates": [
                simplify_line(line, tolerance)
                for line in geometry["coordinates"]
                if len(line) >= 2
            ],
        }

    return geometry


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float] | None:
    """Return min_lon, min_lat, max_lon, max_lat for line geometries."""
    coordinates = geometry.get("coordinates", [])

    if geometry.get("type") == "LineString":
        points = coordinates
    elif geometry.get("type") == "MultiLineString":
        points = [point for line in coordinates for point in line]
    else:
        return None

    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def geometry_intersects_bbox(geometry: dict) -> bool:
    bounds = geometry_bounds(geometry)
    if not bounds:
        return False

    min_x, min_y, max_x, max_y = bounds
    return not (
        max_x < BBOX["min_lon"] or
        min_x > BBOX["max_lon"] or
        max_y < BBOX["min_lat"] or
        min_y > BBOX["max_lat"]
    )


def row_to_properties(row: sqlite3.Row, columns: list[str], geom_column: str) -> dict:
    row_dict = dict(zip(columns, row))
    properties = {
        field: row_dict.get(field)
        for field in USEFUL_FIELDS
        if field in row_dict and field != geom_column
    }
    fclass = str(properties.get("fclass") or "")
    properties["road_group"] = road_group_for_fclass(fclass)
    properties["display_name"] = display_name(properties)
    return properties


def run_with_sqlite_fallback(gpkg_path: Path, layers: list[str], roads_layer: str) -> None:
    print("\nGeoPackage layers:")
    for layer in layers:
        print(f"  - {layer}")

    with sqlite3.connect(gpkg_path) as connection:
        geom_column = geometry_column(connection, roads_layer)
        columns = table_columns(connection, roads_layer)
        print(f"\nReading roads layer: {roads_layer}")
        print_sqlite_preview(connection, roads_layer, geom_column)

        original_count = connection.execute(f'SELECT COUNT(*) FROM "{roads_layer}"').fetchone()[0]
        rtree = rtree_table_name(connection, roads_layer, geom_column)
        useful_columns = [column for column in USEFUL_FIELDS if column in columns]
        select_columns = [f'roads."{column}"' for column in useful_columns] + [f'roads."{geom_column}"']
        select_sql = ", ".join(select_columns)

        fclass_placeholders = ", ".join("?" for _ in MAJOR_FCLASSES)
        parameters = list(MAJOR_FCLASSES)

        if rtree:
            sql = f"""
                SELECT {select_sql}
                FROM "{roads_layer}" AS roads
                JOIN "{rtree}" AS idx ON roads.ROWID = idx.id
                WHERE roads.fclass IN ({fclass_placeholders})
                  AND idx.maxx >= ?
                  AND idx.minx <= ?
                  AND idx.maxy >= ?
                  AND idx.miny <= ?
            """
            parameters.extend([
                BBOX["min_lon"],
                BBOX["max_lon"],
                BBOX["min_lat"],
                BBOX["max_lat"],
            ])
        else:
            print("\nWarning: no RTree spatial index found. Filtering by fclass first, then geometry bbox.")
            sql = f"""
                SELECT {select_sql}
                FROM "{roads_layer}" AS roads
                WHERE roads.fclass IN ({fclass_placeholders})
            """

        query_columns = useful_columns + [geom_column]
        features = []
        for row in connection.execute(sql, parameters):
            row_values = dict(zip(query_columns, row))
            geometry = parse_gpkg_geometry(row_values.get(geom_column))
            if not geometry:
                continue
            if not rtree and not geometry_intersects_bbox(geometry):
                continue
            geometry = simplify_geometry(geometry, SIMPLIFY_TOLERANCE)
            properties = row_to_properties(row, query_columns, geom_column)
            features.append({
                "type": "Feature",
                "properties": properties,
                "geometry": geometry,
            })

    output = {
        "type": "FeatureCollection",
        "name": "major_roads",
        "features": features,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    print_counts(original_count, features, OUTPUT_PATH)


def main() -> None:
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ZIP_PATH
    gpkg_path = unzip_gpkg(zip_path, WORK_DIR)

    layers = list_layers_sqlite(gpkg_path)
    roads_layer = choose_roads_layer(layers)

    if not run_with_geopandas(gpkg_path, layers, roads_layer):
        run_with_sqlite_fallback(gpkg_path, layers, roads_layer)


if __name__ == "__main__":
    main()
