#!/usr/bin/env python3
"""
Generate 100 representative grocery store points for DC-map_main.

The generation boundary is the standardised Sydney metropolitan analysis
boundary used by this project, not the wider Greater Sydney GCCSA boundary.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
SA2_DENSITY_PATH = DATA_DIR / "sa2_population_density.geojson"
OUTPUT_PATH = DATA_DIR / "generated_stores_100_metro.geojson"
PROJECTED_CRS = "EPSG:7856"
RANDOM_SEED = 6201
TOWN_HALL_LAT = -33.8731
TOWN_HALL_LON = 151.2065

BOUNDARY_CANDIDATES = [
    DATA_DIR / "metropolitan_sydney_boundary_dissolved.geojson",
    DATA_DIR / "metropolitan_sydney_boundary.geojson",
    DATA_DIR / "sydney_metropolitan_boundary.geojson",
]

RING_RULES = {
    "Inner": {"min_km": 0, "max_km": 10, "spacing_km": 1.2, "distance_band": "0-10 km"},
    "Middle": {"min_km": 10, "max_km": 30, "spacing_km": 2.5, "distance_band": "10-30 km"},
    "Outer": {"min_km": 30, "max_km": math.inf, "spacing_km": 4.0, "distance_band": ">30 km"},
}

STORE_ALLOCATION = {
    ("Inner", "high"): 14,
    ("Inner", "medium"): 8,
    ("Inner", "low"): 3,
    ("Middle", "high"): 22,
    ("Middle", "medium"): 14,
    ("Middle", "low"): 4,
    ("Outer", "high"): 15,
    ("Outer", "medium"): 14,
    ("Outer", "low"): 6,
}

STORE_SIZE_WEIGHTS = {
    "Inner": [("small", 0.60), ("medium", 0.35), ("large", 0.05)],
    "Middle": [("small", 0.20), ("medium", 0.60), ("large", 0.20)],
    "Outer": [("small", 0.10), ("medium", 0.45), ("large", 0.45)],
}

DELIVERY_FREQUENCY_RANGES = {
    ("Inner", "small"): (8, 10),
    ("Inner", "medium"): (8, 10),
    ("Inner", "large"): (9, 10),
    ("Middle", "small"): (6, 7),
    ("Middle", "medium"): (7, 8),
    ("Middle", "large"): (8, 9),
    ("Outer", "small"): (5, 6),
    ("Outer", "medium"): (6, 8),
    ("Outer", "large"): (7, 9),
}

SA2_CODE_FIELDS = ["SA2_CODE21", "SA2_CODE", "sa2_code"]
SA2_NAME_FIELDS = ["SA2_NAME21", "SA2_NAME", "sa2_name", "name"]
DENSITY_FIELDS = ["population_density", "POPULATION_DENSITY", "density", "pop_density"]
POPULATION_FIELDS = ["population_2021", "POPULATION_2021", "population", "pop", "2021"]
AREA_FIELDS = ["area_sqkm", "AREASQKM21", "AREA_SQKM", "AREASQKM", "area"]


def first_existing(row, fields: Iterable[str], default=None):
    for field in fields:
        if field in row and row[field] is not None and str(row[field]).strip() != "":
            return row[field]
    return default


def choose_boundary_path() -> Path:
    for path in BOUNDARY_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No Sydney metropolitan analysis boundary found in data/.")


def classify_ring(distance_km: float) -> str:
    for ring, rule in RING_RULES.items():
        if rule["min_km"] <= distance_km < rule["max_km"]:
            return ring
    return "Outer"


def classify_density_by_ring(eligible: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    parts = []
    for _, group in eligible.groupby("ring", sort=False):
        ranked = group.sort_values("population_density", ascending=False).copy()
        n = len(ranked)
        high_count = max(1, math.ceil(n * 0.30))
        low_count = max(1, math.floor(n * 0.30))
        medium_count = max(0, n - high_count - low_count)
        ranked["density_class"] = (["high"] * high_count + ["medium"] * medium_count + ["low"] * low_count)[:n]
        parts.append(ranked)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=eligible.crs)


def choose_store_size(ring: str, rng: random.Random) -> str:
    pick = rng.random()
    cumulative = 0.0
    for size, weight in STORE_SIZE_WEIGHTS[ring]:
        cumulative += weight
        if pick <= cumulative:
            return size
    return STORE_SIZE_WEIGHTS[ring][-1][0]


def delivery_frequency(ring: str, size: str, rng: random.Random) -> str:
    low, high = DELIVERY_FREQUENCY_RANGES[(ring, size)]
    value = rng.randint(low, high)
    return f"{value} per week"


def random_point_in_polygon(geometry, rng: random.Random, max_attempts: int = 260):
    minx, miny, maxx, maxy = geometry.bounds
    for _ in range(max_attempts):
        point = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if geometry.contains(point):
            return point
    point = geometry.representative_point()
    return point if geometry.contains(point) else None


def load_inputs():
    if not SA2_DENSITY_PATH.exists():
        raise FileNotFoundError(f"Missing SA2 density file: {SA2_DENSITY_PATH}")

    boundary_path = choose_boundary_path()
    print("Boundary file:", boundary_path, flush=True)

    sa2 = gpd.read_file(SA2_DENSITY_PATH)
    boundary = gpd.read_file(boundary_path)
    if sa2.crs is None:
        sa2 = sa2.set_crs("EPSG:4326")
    if boundary.crs is None:
        boundary = boundary.set_crs("EPSG:4326")

    sa2_projected = sa2.to_crs(PROJECTED_CRS)
    boundary_projected = boundary.to_crs(PROJECTED_CRS)
    metro_geometry = unary_union(boundary_projected.geometry)
    metro_prepared = prep(metro_geometry)
    town_hall = gpd.GeoSeries([Point(TOWN_HALL_LON, TOWN_HALL_LAT)], crs="EPSG:4326").to_crs(PROJECTED_CRS).iloc[0]

    densities = []
    for _, row in sa2_projected.iterrows():
        density = first_existing(row, DENSITY_FIELDS)
        if density is None:
            population = first_existing(row, POPULATION_FIELDS)
            area = first_existing(row, AREA_FIELDS)
            density = float(population) / float(area) if population is not None and area not in (None, 0, "0") else np.nan
        densities.append(float(density) if density is not None else np.nan)

    sa2_projected["population_density"] = densities
    sa2_projected["centroid_projected"] = sa2_projected.geometry.centroid
    sa2_projected["centroid_inside_metro"] = sa2_projected["centroid_projected"].apply(metro_prepared.contains)
    sa2_projected["distance_to_town_hall_km"] = sa2_projected["centroid_projected"].distance(town_hall) / 1000
    sa2_projected["ring"] = sa2_projected["distance_to_town_hall_km"].apply(classify_ring)

    eligible = sa2_projected[
        sa2_projected["centroid_inside_metro"]
        & sa2_projected["population_density"].notna()
        & (
            sa2_projected["population_density"].gt(50)
            | sa2_projected["ring"].eq("Outer")
        )
    ].copy()

    eligible["geometry"] = eligible.geometry.intersection(metro_geometry)
    eligible = eligible[eligible.geometry.notna() & ~eligible.geometry.is_empty].copy()
    eligible = classify_density_by_ring(eligible)
    eligible["sa2_code_clean"] = eligible.apply(lambda row: str(first_existing(row, SA2_CODE_FIELDS, "")), axis=1)
    eligible["sa2_name_clean"] = eligible.apply(lambda row: str(first_existing(row, SA2_NAME_FIELDS, "")), axis=1)
    eligible["current_store_count"] = 0

    high_density_cutoff = eligible["population_density"].quantile(0.90)
    eligible["store_limit"] = 2
    eligible.loc[eligible["population_density"].ge(high_density_cutoff), "store_limit"] = 3
    eligible.loc[eligible["ring"].eq("Outer") & eligible["density_class"].eq("low"), "store_limit"] = 1

    print("SA2 density features loaded:", len(sa2), flush=True)
    print("Eligible SA2s inside Sydney metropolitan boundary:", len(eligible), flush=True)
    print("Eligible SA2s by ring and density class:", flush=True)
    print(eligible.groupby(["ring", "density_class"]).size().to_string(), flush=True)
    return eligible, metro_geometry


def create_candidate_pool(candidates: gpd.GeoDataFrame, rng: random.Random) -> list[dict]:
    pool = []
    max_distance = max(float(candidates["distance_to_town_hall_km"].max()), 1.0)
    for idx, row in candidates.iterrows():
        density = max(float(row["population_density"]), 1.0)
        remote_penalty = 1.0
        if row["ring"] == "Outer":
            remote_penalty = max(0.25, 1.0 - (float(row["distance_to_town_hall_km"]) - 30.0) / max(max_distance, 1.0) * 0.55)
            if density <= 50:
                remote_penalty *= 0.20
        weight = math.sqrt(density) * remote_penalty
        count = int(36 + min(150, math.sqrt(density) * 1.7))
        for _ in range(count):
            point = random_point_in_polygon(row.geometry, rng)
            if point is not None:
                pool.append({"idx": idx, "point": point, "weight": weight})
    rng.shuffle(pool)
    pool.sort(key=lambda item: rng.random() / max(item["weight"], 0.0001))
    return pool


def respects_spacing(point, ring: str, placed_points: list[Point]) -> bool:
    required_m = RING_RULES[ring]["spacing_km"] * 1000
    return all(point.distance(existing) >= required_m for existing in placed_points)


def select_store_location(eligible, ring: str, density_class: str, rng: random.Random, placed_points: list[Point]):
    candidates = eligible[
        eligible["ring"].eq(ring)
        & eligible["density_class"].eq(density_class)
        & eligible["current_store_count"].lt(eligible["store_limit"])
    ]
    if candidates.empty:
        return None, None

    for item in create_candidate_pool(candidates, rng):
        idx = item["idx"]
        if eligible.at[idx, "current_store_count"] >= eligible.at[idx, "store_limit"]:
            continue
        if respects_spacing(item["point"], ring, placed_points):
            return idx, item["point"]
    return None, None


def generate_stores(eligible: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rng = random.Random(RANDOM_SEED)
    records = []
    placed_points = []
    store_number = 1

    generation_order = sorted(
        STORE_ALLOCATION.items(),
        key=lambda item: RING_RULES[item[0][0]]["spacing_km"],
        reverse=True,
    )

    for (ring, density_class), target_count in generation_order:
        print(f"Generating {target_count} {ring} / {density_class} stores...", flush=True)
        generated = 0
        while generated < target_count:
            idx, point = select_store_location(eligible, ring, density_class, rng, placed_points)
            if idx is None:
                raise RuntimeError(f"Could not generate enough stores for {ring}/{density_class}: {generated} of {target_count}.")

            row = eligible.loc[idx]
            store_size = choose_store_size(ring, rng)
            store_id = f"S{store_number:03d}"
            records.append({
                "store_id": store_id,
                "store_name": f"Store {store_id}",
                "ring": ring,
                "distance_band": RING_RULES[ring]["distance_band"],
                "distance_to_town_hall_km": round(float(row["distance_to_town_hall_km"]), 2),
                "sa2_code": row["sa2_code_clean"],
                "sa2_name": row["sa2_name_clean"],
                "population_density": round(float(row["population_density"]), 2),
                "density_class": density_class,
                "store_size": store_size,
                "estimated_delivery_frequency_per_week": delivery_frequency(ring, store_size, rng),
                "geometry": point,
            })
            eligible.at[idx, "current_store_count"] += 1
            placed_points.append(point)
            store_number += 1
            generated += 1

    return gpd.GeoDataFrame(records, geometry="geometry", crs=PROJECTED_CRS)


def validate_and_save(stores_projected: gpd.GeoDataFrame, metro_geometry) -> dict:
    inside = stores_projected.geometry.within(metro_geometry)
    distances = stores_projected["distance_to_town_hall_km"].astype(float)
    stores_wgs84 = stores_projected.to_crs("EPSG:4326")
    stores_wgs84.to_file(OUTPUT_PATH, driver="GeoJSON")

    summary = {
        "total_stores_generated": int(len(stores_projected)),
        "ring_counts": {k: int(v) for k, v in stores_projected.groupby("ring").size().items()},
        "stores_inside_sydney_metropolitan_boundary": int(inside.sum()),
        "stores_outside_boundary": int((~inside).sum()),
        "sa2s_used": int(stores_projected["sa2_code"].nunique()),
        "minimum_distance_to_town_hall_km": round(float(distances.min()), 2),
        "median_distance_to_town_hall_km": round(float(distances.median()), 2),
        "maximum_distance_to_town_hall_km": round(float(distances.max()), 2),
        "output_file_path": str(OUTPUT_PATH),
        "output_file_size_bytes": OUTPUT_PATH.stat().st_size,
    }

    print("\nValidation summary", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    return summary


def main():
    eligible, metro_geometry = load_inputs()
    stores = generate_stores(eligible)
    if len(stores) != 100:
        raise RuntimeError(f"Expected 100 stores, generated {len(stores)}")
    validate_and_save(stores, metro_geometry)


if __name__ == "__main__":
    main()
