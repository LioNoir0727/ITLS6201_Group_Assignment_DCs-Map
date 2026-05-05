# Sydney Grocery DC Location Map

This repository contains an interactive HTML map developed for the ITLS6201 group project.  
The map supports the analysis of distribution centre location decisions for a grocery retail network across Greater Sydney.

## Project Purpose

The purpose of this map is to visualise and support the selection of two distribution centre locations for a 100-store grocery network in Greater Sydney.

The selected distribution centre locations are:

- **DC1: Banksmeadow / Port Botany**
- **DC2: Eastern Creek / Erskine Park**

These two locations are assessed based on their ability to support inbound freight access, store coverage, metropolitan delivery efficiency, and future decarbonisation through battery electric trucks.

## Map Contents

The interactive map may include the following layers:

- Greater Sydney boundary
- 100 grocery store locations
- Candidate distribution centre locations
- Selected distribution centre sites
- Major road network
- Freight and rail-related infrastructure
- Key logistics gateways, such as Port Botany, Sydney Airport, and Moorebank Intermodal Terminal

## Main Analysis Logic

The map is designed to support the following location analysis:

### 1. Inbound Freight Access

Banksmeadow / Port Botany is close to Sydney’s major container port and airport-related freight activity, making it suitable for imported non-perishable goods and some high-value perishable products.

Eastern Creek / Erskine Park is located in Western Sydney, a major logistics and warehousing area with strong access to major motorways and distribution networks.

### 2. Store Coverage

The two-DC structure improves geographic coverage across Greater Sydney.  
An eastern DC supports inner-city, eastern and airport/port-linked flows, while a western DC supports western and outer metropolitan store clusters.

### 3. Road Network Connectivity

The DC locations are positioned near important freight routes and motorways, supporting overnight replenishment and reducing unnecessary cross-city travel.

### 4. Operational Resilience

Using two distribution centres reduces reliance on a single site and improves flexibility if there are disruptions, congestion, or demand spikes in one part of the network.

### 5. Real-World Validation

The selected areas align with real-world logistics patterns in Sydney, where major distribution and warehousing activities are concentrated around Port Botany, Banksmeadow, Eastern Creek, Erskine Park and surrounding freight corridors.

## File Structure

```text
.
├── index.html
├── data/
│   ├── stores.geojson
│   ├── candidate_dc_sites.geojson
│   ├── selected_dc_sites.geojson
│   ├── major_roads.geojson
│   └── rail_lines.geojson
├── assets/
│   └── images or icons used in the map
└── README.md
