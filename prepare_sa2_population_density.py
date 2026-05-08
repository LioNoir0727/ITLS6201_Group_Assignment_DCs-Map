#!/usr/bin/env python3
"""
Prepare a Greater Sydney SA2 population density GeoJSON layer.

Inputs:
  data/sa2.geojson
  /Users/lionoir/Downloads/32180DS0001_2001-21f.xlsx

Output:
  data/sa2_population_density.geojson

The main workflow uses pandas and geopandas. This environment may not have an
Excel engine such as openpyxl installed, so a small XLSX XML reader is included
as a fallback and returns pandas DataFrames.
"""

from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
SA2_GEOJSON_PATH = SCRIPT_DIR / "data" / "sa2.geojson"
EXCEL_PATH = Path("/Users/lionoir/Downloads/32180DS0001_2001-21f.xlsx")
OUTPUT_PATH = SCRIPT_DIR / "data" / "sa2_population_density.geojson"

NAMESPACES = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def normalise_name(value: object) -> str:
    """Normalise column labels for reliable matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def clean_code(value: object) -> str:
    """Convert ABS numeric-looking codes into stable string codes."""
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip()
    if not text:
        return ""

    if re.fullmatch(r"\d+\\.0", text):
        text = text[:-2]

    if isinstance(value, float) and value.is_integer():
        text = str(int(value))

    return text


def column_index(cell_reference: str) -> int:
    letters = "".join(character for character in cell_reference if character.isalpha())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter.upper()) - 64
    return index - 1


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    shared_strings = []
    for item in root.findall("main:si", NAMESPACES):
        text_parts = [
            text.text or ""
            for text in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        shared_strings.append("".join(text_parts))
    return shared_strings


def workbook_sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships
    }

    sheets = []
    for sheet in workbook.find("main:sheets", NAMESPACES):
        sheet_name = sheet.attrib["name"]
        relationship_id = sheet.attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        target = relationship_targets[relationship_id]
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        sheets.append((sheet_name, target))
    return sheets


def cell_value(cell: ET.Element, shared_strings: list[str]) -> object:
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        return "".join(
            text.text or ""
            for text in cell.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        )

    value_node = cell.find("main:v", NAMESPACES)
    if value_node is None or value_node.text is None:
        return ""

    raw_value = value_node.text
    if cell_type == "s":
        return shared_strings[int(raw_value)]
    if cell_type == "str":
        return raw_value

    try:
        if "." in raw_value or "E" in raw_value or "e" in raw_value:
            return float(raw_value)
        return int(raw_value)
    except ValueError:
        return raw_value


def read_sheet_rows(archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[object]]:
    root = ET.fromstring(archive.read(sheet_path))
    rows = []
    for row in root.findall("main:sheetData/main:row", NAMESPACES):
        values_by_column = {}
        max_column = -1
        for cell in row.findall("main:c", NAMESPACES):
            index = column_index(cell.attrib.get("r", "A1"))
            values_by_column[index] = cell_value(cell, shared_strings)
            max_column = max(max_column, index)

        if max_column >= 0:
            rows.append([values_by_column.get(index, "") for index in range(max_column + 1)])
        else:
            rows.append([])
    return rows


def make_unique_columns(columns: list[str]) -> list[str]:
    seen = {}
    unique_columns = []
    for column in columns:
        clean_column = str(column).strip() or "blank"
        if clean_column not in seen:
            seen[clean_column] = 0
            unique_columns.append(clean_column)
        else:
            seen[clean_column] += 1
            unique_columns.append(f"{clean_column}_{seen[clean_column]}")
    return unique_columns


def build_columns_from_abs_rows(year_row: list[object], header_row: list[object], group_row: list[object]) -> list[str]:
    max_length = max(len(year_row), len(header_row), len(group_row))
    columns = []

    for index in range(max_length):
        year_value = year_row[index] if index < len(year_row) else ""
        header_value = header_row[index] if index < len(header_row) else ""
        group_value = group_row[index] if index < len(group_row) else ""

        if header_value and str(header_value).strip().lower() not in {"no.", "%", "km2", "persons/km2"}:
            columns.append(str(header_value).strip())
        elif year_value:
            columns.append(str(int(year_value)) if isinstance(year_value, float) and year_value.is_integer() else str(year_value).strip())
        elif group_value and header_value:
            columns.append(f"{group_value} {header_value}".strip())
        elif group_value:
            columns.append(str(group_value).strip())
        elif header_value:
            columns.append(str(header_value).strip())
        else:
            columns.append(f"blank_{index}")

    return make_unique_columns(columns)


def rows_to_dataframe(rows: list[list[object]]) -> pd.DataFrame:
    """Convert ABS sheet rows into a rectangular DataFrame with useful headers."""
    header_index = None
    for index, row in enumerate(rows):
        normalised_values = {normalise_name(value) for value in row}
        if "stcode" in normalised_values and "stname" in normalised_values:
            header_index = index
            break

    if header_index is None:
        max_length = max((len(row) for row in rows), default=0)
        padded_rows = [row + [""] * (max_length - len(row)) for row in rows]
        return pd.DataFrame(padded_rows)

    group_row = rows[header_index - 2] if header_index >= 2 else []
    year_row = rows[header_index - 1] if header_index >= 1 else []
    header_row = rows[header_index]
    columns = build_columns_from_abs_rows(year_row, header_row, group_row)
    max_length = len(columns)

    records = []
    for row in rows[header_index + 1:]:
        padded_row = row + [""] * (max_length - len(row))
        clipped_row = padded_row[:max_length]
        if any(str(value).strip() for value in clipped_row):
            records.append(clipped_row)

    return pd.DataFrame(records, columns=columns)


def read_excel_sheets(excel_path: Path) -> dict[str, pd.DataFrame]:
    """Read sheets with pandas; fall back to XML parsing if no Excel engine exists."""
    try:
        workbook = pd.ExcelFile(excel_path)
        print(f"Excel engine: {workbook.engine}")
        sheets = {}
        for sheet_name in workbook.sheet_names:
            raw_rows = pd.read_excel(excel_path, sheet_name=sheet_name, header=None).fillna("").values.tolist()
            sheets[sheet_name] = rows_to_dataframe(raw_rows)
        return sheets
    except ImportError as error:
        print(f"pandas could not load the Excel engine ({error}). Using XLSX XML fallback.")

    with zipfile.ZipFile(excel_path) as archive:
        shared_strings = read_shared_strings(archive)
        sheets = {}
        for sheet_name, sheet_path in workbook_sheet_paths(archive):
            rows = read_sheet_rows(archive, sheet_path, shared_strings)
            sheets[sheet_name] = rows_to_dataframe(rows)
        return sheets


def find_column(columns: list[str], candidates: list[str]) -> str:
    lookup = {normalise_name(column): column for column in columns}
    for candidate in candidates:
        normalised_candidate = normalise_name(candidate)
        if normalised_candidate in lookup:
            return lookup[normalised_candidate]

    for column in columns:
        normalised_column = normalise_name(column)
        if any(normalise_name(candidate) in normalised_column for candidate in candidates):
            return column

    raise ValueError(f"Could not find a column matching: {candidates}")


def find_sa2_population_table(sheets: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame, str, str]:
    for sheet_name, dataframe in sheets.items():
        columns = list(map(str, dataframe.columns))
        try:
            sa2_code_column = find_column(columns, ["SA2 code", "SA2_CODE21", "SA2_CODE"])
            population_column = find_column(columns, ["2021"])
            return sheet_name, dataframe, sa2_code_column, population_column
        except ValueError:
            continue

    raise ValueError("Could not identify a sheet containing both SA2 code and 2021 population columns.")


def print_sheet_inspection(sheets: dict[str, pd.DataFrame]) -> None:
    print("Sheet names:")
    for sheet_name in sheets:
        print(f"  - {sheet_name}")

    print("\nColumns by sheet:")
    for sheet_name, dataframe in sheets.items():
        print(f"\n[{sheet_name}]")
        print(list(map(str, dataframe.columns)))
        print(dataframe.head(10).to_string(index=False))


def main() -> None:
    excel_path = Path(sys.argv[1]) if len(sys.argv) > 1 else EXCEL_PATH

    print(f"Reading SA2 GeoJSON: {SA2_GEOJSON_PATH}")
    sa2 = gpd.read_file(SA2_GEOJSON_PATH)
    print(f"SA2 GeoJSON columns: {list(sa2.columns)}")

    print(f"\nReading Excel workbook: {excel_path}")
    sheets = read_excel_sheets(excel_path)
    print_sheet_inspection(sheets)

    sheet_name, population_table, sa2_code_column, population_column = find_sa2_population_table(sheets)
    print(f"\nSelected population sheet: {sheet_name}")
    print(f"Identified SA2 code field: {sa2_code_column}")
    print(f"Identified 2021 population field: {population_column}")

    geo_sa2_code_column = find_column(list(sa2.columns), ["SA2_CODE21", "SA2 code", "SA2_CODE"])
    area_column = find_column(list(sa2.columns), ["AREASQKM21", "AREASQKM", "area_sqkm"])
    print(f"GeoJSON SA2 code field: {geo_sa2_code_column}")
    print(f"GeoJSON area field: {area_column}")

    population_lookup = population_table[[sa2_code_column, population_column]].copy()
    population_lookup["sa2_join_code"] = population_lookup[sa2_code_column].apply(clean_code)
    population_lookup["population_2021"] = pd.to_numeric(population_lookup[population_column], errors="coerce")
    population_lookup = population_lookup[population_lookup["sa2_join_code"] != ""]
    population_lookup = population_lookup.drop_duplicates("sa2_join_code").set_index("sa2_join_code")

    sa2["sa2_join_code"] = sa2[geo_sa2_code_column].apply(clean_code)

    gcc_name_columns = [column for column in ["GCC_NAME21", "GCCSA_NAME21", "GCC_NAME", "GCCSA_NAME"] if column in sa2.columns]
    if gcc_name_columns:
        gcc_column = gcc_name_columns[0]
        sa2 = sa2[sa2[gcc_column].astype(str).str.contains("Greater Sydney", case=False, na=False)].copy()
        print(f"Filtered to Greater Sydney using {gcc_column}.")
    else:
        print("No GCCSA name field found; keeping SA2 GeoJSON as supplied.")

    sa2 = sa2.merge(
        population_lookup[["population_2021"]],
        how="left",
        left_on="sa2_join_code",
        right_index=True,
    )
    sa2["area_sqkm"] = pd.to_numeric(sa2[area_column], errors="coerce")
    sa2["population_density"] = sa2["population_2021"] / sa2["area_sqkm"]

    matched_count = int(sa2["population_2021"].notna().sum())
    missing_count = int(sa2["population_2021"].isna().sum())
    density = sa2["population_density"].dropna()

    output_columns = [
        column for column in sa2.columns
        if column not in {"sa2_join_code"} and column != "geometry"
    ] + ["geometry"]
    output = sa2[output_columns].copy()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output.to_file(OUTPUT_PATH, driver="GeoJSON")

    print("\nPopulation density output summary:")
    print(f"Number of SA2 features: {len(sa2):,}")
    print(f"Number successfully matched with population: {matched_count:,}")
    print(f"Number missing population: {missing_count:,}")
    if density.empty:
        print("Population density min/max/median: no matched density values")
    else:
        print(f"Minimum population density: {density.min():,.2f} persons/km2")
        print(f"Maximum population density: {density.max():,.2f} persons/km2")
        print(f"Median population density: {density.median():,.2f} persons/km2")
    print(f"Saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
