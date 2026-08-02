#!/usr/bin/env python3

import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np

# ----------------------------
# Load Luxembourg outline
# ----------------------------

lu = gpd.read_file("lu.json")

# Force CRS if not present
if lu.crs is None:
    lu = lu.set_crs("EPSG:2169")

# WGS84 -> LUREF8
lu = lu.to_crs("EPSG:2169")


# ----------------------------
# Load Bergerhoff stations
# ----------------------------

rows = []
with open("network-h-bergerhoff.csv", encoding="utf-8") as f:
    # skip header
    next(f)
    for line in f:

        parts = line.split(";")

        # we only need the first 8 columns
        rows.append({
            "reseau": parts[0].strip('"'),
            "sous_reseau": parts[1].strip('"'),
            "station": parts[2].strip('"'),
            "x": parts[6].strip('"'),
            "y": parts[7].strip('"'),
        })
stations = pd.DataFrame(rows)

rows = []
with open("bergerhoff-2026.csv", encoding="utf-8") as f:
    # skip header
    next(f)
    for line in f:

        parts = line.split(";")

        # we only need the first 8 columns
        rows.append({
            "station": parts[0].strip('"'),
            "key":     parts[4].strip('"'),
            "units":   parts[6].strip('"'),
            "value":   parts[7].strip('"')
        })
data = pd.DataFrame(rows)



stations["x"] = pd.to_numeric(stations["x"], errors="coerce")
stations["y"] = pd.to_numeric(stations["y"], errors="coerce")
data["value"] = pd.to_numeric(data["value"])

merged = stations.merge( data, on="station", how="inner" )
print( merged.head )
stations = merged

# Create geometry from LUREF coordinates
gdf = gpd.GeoDataFrame(
    stations,
    geometry=gpd.points_from_xy(
        stations["x"],
        stations["y"]
    ),
    crs="EPSG:2169"
)

### prepare data:
key       = "Pb"
plotrows  = []
scale_max = 100.0

for k, val, x, y in zip( stations["key"], 
                         stations["value"],
                         stations["x"],
                         stations["y"] ):
    if k != key: continue
    plotrows.append( ( x, y, val / scale_max ) )

# ----------------------------
# Plot
# ----------------------------

fig, axes = plt.subplots( 2, 1, figsize=(7, 14))

marksize=55
for ax in axes:
    lu.plot( ax=ax, facecolor="white", edgecolor="black", linewidth=1)

    ##show the data   
    for  (x,y,v) in plotrows:
        ax.scatter( x, y,  s=marksize, color="none", edgecolor="black", zorder=2 )
        ax.scatter( x, y, color="red", s=marksize, alpha=min(1.,v), zorder=3 )

    # remove border
    for spine in ax.spines.values():
        spine.set_visible(False)
    
    ax.set_xticks([])
    ax.set_yticks([])

    ax.set_aspect("equal")


axes[0].set_title("Airbourne Lead Pollution in Luxembourg 2026")

info = (
"""
 All Bergerhoff measurement sites
 Average annual deposition as collected 29/7/2026
 Opacity indicates concentration
        (saturated at 100 µg/m²/day)
 source data: 
 https://data.public.lu/fr/datasets/r ...
  ... /1feb7201-0b3e-45a3-9f03-f694d51b761e
"""
)
axes[0].text(
    0.62, 0.88,
    info,
    transform=axes[0].transAxes,
    va="top",
    ha="left",
    fontsize=9,
    bbox=dict(
        facecolor="white",
        edgecolor="black",
        alpha=0.85,
        boxstyle="round,pad=0.4"
    ),
    zorder=100
)


##focus on "Sale Sud"
axes[1].set_xlim(52000, 75000)
axes[1].set_ylim(55000, 80000)

##add towns
towns = [
    ("Luxembourg", 76500, 74500),
    ("Esch-sur-Alzette", 66200, 63400),
    ("Differdange", 60800, 65400),
    ("Dudelange", 66500, 58500),
    ("Ettelbruck", 75500, 101500),
    ("Diekirch", 80000, 103000),
    ("Wiltz", 61000, 115000),
    ("Remich", 94500, 68000),
    ("Grevenmacher", 102000, 84000), ]
for name, x, y in towns:
    ax.scatter(
        x, y,
        marker="+",
        color="blue",
        s=60,
        zorder=4)
    ax.annotate(
        name,
        (x, y),
        xytext=(5, 5),
        textcoords="offset points",
        fontsize=8,
        color="blue",
        zorder=5 )


plt.tight_layout()
plt.savefig("bergerhoff.pdf")
plt.savefig("bergerhoff.png")
