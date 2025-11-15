"""
spatial_map_v22_no_search_dropdown_with_types.py

Version: v22 with added hospital-type filters.

What this file does (changes since v22 base):
- Adds a color-by-type layer "Hospitals (colored by type)" where markers are colored:
    - 'รัฐ'  -> light green (#66bb6a)
    - 'เอกชน' -> pink (#ff80b3)
    - unknown/other -> gray (#9E9E9E)
  This layer groups all hospitals and colors each marker by its 'ประเภท' column.
- Adds a filter "Hospitals - รัฐ" (show only public/state hospitals) (show=False).
- Adds a filter "Hospitals - เอกชน" (show only private hospitals) (show=False).
- Each of the two per-type filters shows markers like the Hospitals Only layer,
  but restricted to that hospital type and includes the same popup structure.
- Keeps all original layers and behaviors (heat, size, choropleth, bounds, click-highlight).
- Defensive: if column "ประเภท" is missing, all hospitals are treated as 'unknown' and colored gray.

Usage:
    python spatial_map_v22_no_search_dropdown_with_types.py

Place required inputs in same folder:
  - hospitals.csv
  - communities.csv
  - districts_bangkok.geojson

Output:
  - spatial_map_v22_no_search_dropdown.html
"""
import json
import math
import pandas as pd
import folium
from folium.plugins import HeatMap
from folium.features import GeoJsonTooltip, GeoJsonPopup
from geopy.distance import geodesic
from shapely.geometry import shape, Point
import branca.colormap as cm

# -------------------------
# Config / Data load
# -------------------------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "spatial_map_v22_no_search_dropdown.html"

# Load CSVs
hospitals = pd.read_csv(HOSPITALS_CSV)
communities = pd.read_csv(COMMUNITIES_CSV)

# Basic expected coordinate column names (adjust if your CSV uses different headers)
lat_col = 'ละติจูด'
lon_col = 'ลองจิจูด'
if lat_col not in hospitals.columns or lon_col not in hospitals.columns:
    raise KeyError(f"Expected hospital coords columns '{lat_col}', '{lon_col}' in {HOSPITALS_CSV}")
if lat_col not in communities.columns or lon_col not in communities.columns:
    raise KeyError(f"Expected community coords columns '{lat_col}', '{lon_col}' in {COMMUNITIES_CSV}")

# Clean column names (strip whitespace / BOM)
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

# Try to detect the hospital name and community name column names robustly
possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
possible_comm_name_cols = ['ชุมชน', 'ชื่อชุมชน', 'community', 'name', 'ชื่อ']

hosp_name_col = None
for c in possible_hosp_name_cols:
    if c in hospitals.columns:
        hosp_name_col = c
        break
if hosp_name_col is None:
    hosp_name_col = hospitals.columns[0]
    print(f"Warning: hospital name column not found among expected names, using '{hosp_name_col}' as fallback")

comm_name_col = None
for c in possible_comm_name_cols:
    if c in communities.columns:
        comm_name_col = c
        break
if comm_name_col is None:
    comm_name_col = communities.columns[0]
    print(f"Warning: community name column not found among expected names, using '{comm_name_col}' as fallback")

# Optionally trim values in the detected name columns (if they exist)
try:
    hospitals[hosp_name_col] = hospitals[hosp_name_col].astype(str).str.strip()
except Exception:
    pass
try:
    communities[comm_name_col] = communities[comm_name_col].astype(str).str.strip()
except Exception:
    pass

# -------------------------
# Create map (CartoDB Positron)
# -------------------------
m = folium.Map(
    location=[communities[lat_col].mean(), communities[lon_col].mean()],
    zoom_start=12,
    tiles=None
)
folium.TileLayer(
    tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
    name='CartoDB Positron',
    control=True,
    show=True
).add_to(m)
folium.TileLayer('OpenStreetMap', name='OpenStreetMap', show=False).add_to(m)

# -------------------------
# All Hospitals baseline layer (markers + community->nearest lines)
# NOTE: do NOT show by default
# -------------------------
all_layer = folium.FeatureGroup(name="All Hospitals", show=False).add_to(m)
for _, row in hospitals.iterrows():
    popup_hosp = (
        f"<div style=\"background:white; padding:8px; font-size:13px; border-radius:8px;\">"
        f"<b>🏥 {row.get(hosp_name_col, '')}</b><br>"
        f"สิทธิบัตรทอง: {row.get('สิทธิบัตรทอง', '')}<br>"
        f"สิทธิประกันสังคม: {row.get('สิทธิประกันสังคม', '')}<br>"
        f"สิทธิข้าราชการ: {row.get('สิทธิข้าราชการ', '')}"
        f"</div>"
    )
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        color='red', radius=7,
        popup=folium.Popup(popup_hosp, max_width=300),
        tooltip=row.get(hosp_name_col, ''),
        fill=True, fill_opacity=0.8
    ).add_to(all_layer)

# -------------------------
# Hospitals Only filter (shows ONLY hospital markers)
# DEFAULT VISIBLE
# -------------------------
hospitals_only_layer = folium.FeatureGroup(name="Hospitals Only", show=True).add_to(m)
for _, row in hospitals.iterrows():
    popup_hosp = (
        f"🏥 {row.get(hosp_name_col, '')}"
        f"<br>สิทธิบัตรทอง: {row.get('สิทธิบัตรทอง','')}"
        f"<br>สิทธิประกันสังคม: {row.get('สิทธิประกันสังคม','')}"
        f"<br>สิทธิข้าราชการ: {row.get('สิทธิข้าราชการ','')}"
    )
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=7,
        color='#d32f2f',
        fill=True, fill_color='#d32f2f', fill_opacity=0.9,
        popup=folium.Popup(popup_hosp, max_width=280),
        tooltip=row.get(hosp_name_col, '')
    ).add_to(hospitals_only_layer)

# -------------------------
# Hospitals-only marker layers per right (as before)
# -------------------------
rights = {"สิทธิบัตรทอง": "orange", "สิทธิประกันสังคม": "green", "สิทธิข้าราชการ": "purple"}
for right, color in rights.items():
    layer = folium.FeatureGroup(name=f"Hospitals - {right}", show=False).add_to(m)
    eligible = hospitals[hospitals[right] == "YES"] if right in hospitals.columns else hospitals.iloc[0:0]
    for _, row in eligible.iterrows():
        folium.CircleMarker(
            location=[row[lat_col], row[lon_col]],
            radius=7,
            color=color,
            fill=True, fill_color=color, fill_opacity=0.9,
            popup=folium.Popup(f"🏥 {row.get(hosp_name_col,'')}<br>รับสิทธิ: {right}", max_width=300),
            tooltip=f"{row.get(hosp_name_col,'')} — {right}"
        ).add_to(layer)

# -------------------------
# Communities baseline + compute nearest-assignment (nearest hospital index per community)
# -------------------------
comm_assigned = []  # (comm_idx, nearest_hosp_idx, distance_m)
for c_idx, comm in communities.iterrows():
    min_dist = float('inf')
    nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        d = geodesic((comm[lat_col], comm[lon_col]), (hosp[lat_col], hosp[lon_col])).meters
        if d < min_dist:
            min_dist = d
            nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_dist))

# Draw baseline lines (communities -> nearest hospital) and community markers on all_layer
for c_idx, h_idx, dist in comm_assigned:
    comm = communities.loc[c_idx]
    hosp = hospitals.loc[h_idx]
    hosp_name = hosp.get(hosp_name_col, '')
    comm_name = comm.get(comm_name_col, '')
    popup_html = (
        f"<b>ชุมชน:</b> {comm_name}"
        f"<br><b>โรงพยาบาลใกล้ที่สุด:</b> {hosp_name}"
        f"<br><b>ระยะ:</b> {dist:.0f} m"
    )
    folium.PolyLine(
        locations=[[comm[lat_col], comm[lon_col]], [hosp[lat_col], hosp[lon_col]]],
        color='gray', weight=1.5, opacity=0.5
    ).add_to(all_layer)
    folium.CircleMarker(
        location=[comm[lat_col], comm[lon_col]],
        color='blue', radius=4.5,
        popup=folium.Popup(popup_html, max_width=320),
        tooltip=comm_name,
        fill=True, fill_opacity=0.8
    ).add_to(all_layer)

# Communities standalone layer (toggleable)
community_layer = folium.FeatureGroup(name="Communities", show=False).add_to(m)
for _, row in communities.iterrows():
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        color='blue', radius=4,
        popup=folium.Popup(f"ชุมชน: {row.get(comm_name_col,'')}", max_width=280),
        tooltip=row.get(comm_name_col, ''),
        fill=True, fill_opacity=0.7
    ).add_to(community_layer)

# -------------------------
# NEW: Connections layers (one per right) that include:
#  - polylines from each community to nearest eligible hospital for that right
#  - community markers (blue) and eligible hospital markers (colored) inside the same layer
# -------------------------
for right, color in rights.items():
    conn_layer = folium.FeatureGroup(name=f"Connections - {right}", show=False).add_to(m)
    eligible = hospitals[hospitals[right] == "YES"] if right in hospitals.columns else hospitals.iloc[0:0]
    if eligible.empty:
        continue

    added_hospital_idxs = set()

    for c_idx, comm in communities.iterrows():
        min_dist = float('inf')
        nearest_idx = None
        for h_idx, hosp in eligible.iterrows():
            d = geodesic((comm[lat_col], comm[lon_col]), (hosp[lat_col], hosp[lon_col])).meters
            if d < min_dist:
                min_dist = d
                nearest_idx = hosp.name
        if nearest_idx is None:
            continue
        hosp_row = hospitals.loc[nearest_idx]
        comm_name = comm.get(comm_name_col, '')
        folium.PolyLine(
            locations=[[comm[lat_col], comm[lon_col]], [hosp_row[lat_col], hosp_row[lon_col]]],
            color=color, weight=2, opacity=0.7
        ).add_to(conn_layer)
        folium.CircleMarker(
            location=[comm[lat_col], comm[lon_col]],
            radius=4.5,
            color='blue', fill=True, fill_color='blue', fill_opacity=0.8,
            popup=folium.Popup(f"ชุมชน: {comm_name}<br>เชื่อมกับ ({right}): {hosp_row.get(hosp_name_col,'')}", max_width=300),
            tooltip=comm_name
        ).add_to(conn_layer)
        if nearest_idx not in added_hospital_idxs:
            added_hospital_idxs.add(nearest_idx)
            folium.CircleMarker(
                location=[hosp_row[lat_col], hosp_row[lon_col]],
                radius=7,
                color=color, fill=True, fill_color=color, fill_opacity=0.95,
                popup=folium.Popup(f"🏥 {hosp_row.get(hosp_name_col,'')}<br>รับสิทธิ: {right}", max_width=300),
                tooltip=f"{hosp_row.get(hosp_name_col,'')} — {right}"
            ).add_to(conn_layer)

# -------------------------
# Compute hospital weights (nearest-assignment) for marker-size layer
# -------------------------
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    hospitals.at[h_idx, 'weight'] += 1

# -------------------------
# HeatMap featuregroup: keep HeatMap but ALSO show hospital markers in the same layer
# NOTE: not visible by default
# -------------------------
heat_layer = folium.FeatureGroup(name="Hospitals Heat (by nearby communities)", show=False).add_to(m)
heat_data = [[row[lat_col], row[lon_col], row['weight']] for _, row in hospitals.iterrows()]
HeatMap(heat_data, radius=25, blur=15, max_zoom=12).add_to(heat_layer)

# Add hospital markers into the heat layer (so toggling heat shows markers too)
for _, row in hospitals.iterrows():
    w = int(row.get('weight', 0))
    popup_h = f"🏥 {row.get(hosp_name_col,'')}<br>จำนวนชุมชน: {w}"
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=6,
        color='#b71c1c',
        fill=True, fill_color='#b71c1c', fill_opacity=0.85,
        popup=folium.Popup(popup_h, max_width=260),
        tooltip=f"{row.get(hosp_name_col,'')} — weight: {w}"
    ).add_to(heat_layer)

# -------------------------
# Hospitals marker-size layer (size ~ weight) with color intensity
# This layer shows sized circle + a red center marker (radius=7)
# NOTE: not visible by default
# -------------------------
size_layer = folium.FeatureGroup(name="Hospitals (by nearby communities) - marker size", show=False).add_to(m)

# Helper color functions: interpolate between two hex colors
def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*[int(max(0, min(255, round(v)))) for v in rgb])

def mix_colors(hex1, hex2, t):
    """Linear interpolate between hex1 and hex2 by t in [0,1]."""
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    r = r1 + (r2 - r1) * t
    g = g1 + (g2 - g1) * t
    b = b1 + (b2 - b1) * t
    return rgb_to_hex((r, g, b))

small_color_hex = '#ffdede'  # pale red / pink
large_color_hex = '#b71c1c'  # deep red

min_radius = 6
max_radius = 36
max_w = hospitals['weight'].max() if len(hospitals) > 0 else 0

for _, row in hospitals.iterrows():
    w = int(row['weight'])
    normalized = (math.sqrt(w) / math.sqrt(max_w)) if max_w > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized
    popup_hosp = f"🏥 {row.get(hosp_name_col,'')}<br>จำนวนชุมชน: {w}"
    # 1) Big sized circle (as before)
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=radius,
        color=color_hex,
        fill=True, fill_color=color_hex, fill_opacity=fill_opacity,
        weight=stroke_weight,
        popup=folium.Popup(popup_hosp, max_width=300),
        tooltip=f"{row.get(hosp_name_col,'')} — {w} ชุมชน"
    ).add_to(size_layer)
    # 2) Red center marker to indicate exact coordinate (so large radius doesn't hide location)
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=7,
        color='#d32f2f', fill=True, fill_color='#d32f2f', fill_opacity=1.0,
        weight=0.6,
        tooltip=f"{row.get(hosp_name_col,'')} (center)"
    ).add_to(size_layer)

# -------------------------
# NEW: Hospitals (by nearby population) - marker size & color intensity
# Uses hospitals.csv column "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
# -------------------------
pop_col_name = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
hospitals['near_pop'] = pd.to_numeric(hospitals.get(pop_col_name, 0), errors='coerce').fillna(0).astype(int)
max_pop = hospitals['near_pop'].max() if len(hospitals) > 0 else 0
pop_layer = folium.FeatureGroup(name="Hospitals (by nearby population) - marker size", show=False).add_to(m)

for _, row in hospitals.iterrows():
    val = int(row.get('near_pop', 0))
    normalized = (math.sqrt(val) / math.sqrt(max_pop)) if max_pop > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized
    popup_hosp = f"🏥 {row.get(hosp_name_col,'')}<br>จำนวนประชากรใกล้เคียงที่ต้องรองรับ: {val}"
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=radius,
        color=color_hex,
        fill=True, fill_color=color_hex, fill_opacity=fill_opacity,
        weight=stroke_weight,
        popup=folium.Popup(popup_hosp, max_width=300),
        tooltip=f"{row.get(hosp_name_col,'')} — {val} คน"
    ).add_to(pop_layer)
    # center marker
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=7,
        color='#d32f2f', fill=True, fill_color='#d32f2f', fill_opacity=1.0,
        weight=0.6,
        tooltip=f"{row.get(hosp_name_col,'')} (center)"
    ).add_to(pop_layer)

# -------------------------
# NEW: Hospitals (by beds) - marker size & color intensity
# Uses hospitals.csv column "จำนวนเตียง"
# -------------------------
beds_col_name = "จำนวนเตียง"
hospitals['beds'] = pd.to_numeric(hospitals.get(beds_col_name, 0), errors='coerce').fillna(0).astype(int)
max_beds = hospitals['beds'].max() if len(hospitals) > 0 else 0
beds_layer = folium.FeatureGroup(name="Hospitals (by beds) - marker size", show=False).add_to(m)

for _, row in hospitals.iterrows():
    val = int(row.get('beds', 0))
    normalized = (math.sqrt(val) / math.sqrt(max_beds)) if max_beds > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized
    popup_hosp = f"🏥 {row.get(hosp_name_col,'')}<br>จำนวนเตียง: {val}"
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=radius,
        color=color_hex,
        fill=True, fill_color=color_hex, fill_opacity=fill_opacity,
        weight=stroke_weight,
        popup=folium.Popup(popup_hosp, max_width=300),
        tooltip=f"{row.get(hosp_name_col,'')} — {val} เตียง"
    ).add_to(beds_layer)
    # center marker
    folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=7,
        color='#d32f2f', fill=True, fill_color='#d32f2f', fill_opacity=1.0,
        weight=0.6,
        tooltip=f"{row.get(hosp_name_col,'')} (center)"
    ).add_to(beds_layer)

# -------------------------
# NEW: Hospitals colored by type + two per-type filters
# - color_by_type_layer: markers colored by hospitals['ประเภท']
# - hospitals_state_layer: only 'รัฐ'
# - hospitals_private_layer: only 'เอกชน'
# -------------------------
type_col = "ประเภท"
# define colors
TYPE_COLOR_MAP = {
    "รัฐ": "#66bb6a",      # light green
    "เอกชน": "#ff80b3",    # pink
}
UNKNOWN_TYPE_COLOR = "#9E9E9E"  # gray

# make sure the column exists (add as 'unknown' if missing)
if type_col not in hospitals.columns:
    hospitals[type_col] = "unknown"
else:
    # normalize whitespace
    hospitals[type_col] = hospitals[type_col].astype(str).str.strip()

color_by_type_layer = folium.FeatureGroup(name="Hospitals (colored by type)", show=False).add_to(m)
hospitals_state_layer = folium.FeatureGroup(name="Hospitals - รัฐ (public only)", show=False).add_to(m)
hospitals_private_layer = folium.FeatureGroup(name="Hospitals - เอกชน (private only)", show=False).add_to(m)

for _, row in hospitals.iterrows():
    hosp_type = (row.get(type_col) or "").strip()
    color = TYPE_COLOR_MAP.get(hosp_type, UNKNOWN_TYPE_COLOR)
    popup_hosp = (
        f"🏥 {row.get(hosp_name_col,'')}"
        f"<br>ประเภท: {hosp_type}"
        f"<br>จำนวนเตียง: {row.get('จำนวนเตียง', '')}"
    )
    # common marker (radius 7)
    marker = folium.CircleMarker(
        location=[row[lat_col], row[lon_col]],
        radius=7,
        color=color,
        fill=True, fill_color=color, fill_opacity=0.95,
        popup=folium.Popup(popup_hosp, max_width=300),
        tooltip=f"{row.get(hosp_name_col,'')} — {hosp_type}"
    )
    marker.add_to(color_by_type_layer)

    # add to specific type layers as needed
    if hosp_type == "รัฐ":
        folium.CircleMarker(
            location=[row[lat_col], row[lon_col]],
            radius=7,
            color=TYPE_COLOR_MAP["รัฐ"],
            fill=True, fill_color=TYPE_COLOR_MAP["รัฐ"], fill_opacity=0.95,
            popup=folium.Popup(popup_hosp, max_width=300),
            tooltip=f"{row.get(hosp_name_col,'')} — รัฐ"
        ).add_to(hospitals_state_layer)
    elif hosp_type == "เอกชน":
        folium.CircleMarker(
            location=[row[lat_col], row[lon_col]],
            radius=7,
            color=TYPE_COLOR_MAP["เอกชน"],
            fill=True, fill_color=TYPE_COLOR_MAP["เอกชน"], fill_opacity=0.95,
            popup=folium.Popup(popup_hosp, max_width=300),
            tooltip=f"{row.get(hosp_name_col,'')} — เอกชน"
        ).add_to(hospitals_private_layer)
    else:
        # unknown type: do not add to state/private specific layers
        pass

# -------------------------
# District GeoJSON: load and compute metrics (choropleth)
# NOTE: choropleth layer NOT visible by default per request
# -------------------------
with open(GEOJSON_PATH, 'r', encoding='utf-8') as f:
    bangkok_geo = json.load(f)

district_features = bangkok_geo.get('features', [])
district_name_field = 'amp_th'  # adjust if your geojson uses a different property name

district_shapes = [shape(feat['geometry']) for feat in district_features]

# assign hospitals to districts (point-in-polygon)
hospitals['district'] = None
for h_idx, hosp in hospitals.iterrows():
    pt = Point(hosp[lon_col], hosp[lat_col])
    for i, feat in enumerate(district_features):
        if district_shapes[i].contains(pt):
            hospitals.at[h_idx, 'district'] = feat['properties'].get(district_name_field)
            break

# district metrics
district_metrics = {}
for feat in district_features:
    name = feat['properties'].get(district_name_field)
    district_metrics[name] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}
for _, h in hospitals.iterrows():
    d = h['district']
    if pd.notnull(d):
        district_metrics[d]['num_hospitals'] += 1
        district_metrics[d]['sum_hospital_weights'] += int(h.get('weight', 0))
for _, comm in communities.iterrows():
    pt = Point(comm[lon_col], comm[lat_col])
    for i, feat in enumerate(district_features):
        if district_shapes[i].contains(pt):
            name = feat['properties'].get(district_name_field)
            district_metrics[name]['num_communities'] += 1
            break
max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)
for feat in district_features:
    name = feat['properties'].get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / max_sum_weights) if max_sum_weights > 0 else 0.0

colormap = cm.LinearColormap(['#2ecc71', '#ffd54f', '#e74c3c'], vmin=0, vmax=1)

def choro_style(feature):
    props = feature.get('properties', {}) or {}
    numh = props.get('num_hospitals', 0)
    # If no hospitals in district -> gray
    if numh == 0:
        return {
            'fillColor': '#9E9E9E',  # gray
            'color': '#444444',
            'weight': 1.4,
            'fillOpacity': 0.65,
            'opacity': 0.8
        }
    norm = props.get('choropleth_norm', 0.0)
    color = colormap(norm)
    return {
        'fillColor': color,
        'color': '#444444',
        'weight': 1.4,
        'fillOpacity': 0.65,
        'opacity': 0.8
    }

choropleth_layer = folium.FeatureGroup(name="District Choropleth (by hospital weights)", show=False).add_to(m)
folium.GeoJson(
    data=bangkok_geo,
    name="District Choropleth",
    style_function=choro_style,
    highlight_function=lambda f: {'weight':2.8, 'color':'#000000', 'fillOpacity':0.85},
    tooltip=GeoJsonTooltip(
        fields=[district_name_field, 'sum_hospital_weights', 'num_hospitals', 'num_communities'],
        aliases=['เขต:', 'sum weights:', '# hospitals:', '# communities:'],
        localize=True
    ),
).add_to(choropleth_layer)

# -------------------------
# District boundaries layer (thicker stroke) - visible by default
# We capture the GeoJson object reference (gj_bounds) so we can bind handlers directly in JS.
# -------------------------
def bounds_style(feature):
    return {'fillColor': 'transparent', 'color': '#2c3e50', 'weight': 2.8, 'opacity': 0.9}

districts_layer = folium.FeatureGroup(name="Bangkok Districts (bounds)", show=True).add_to(m)
gj_bounds = folium.GeoJson(
    data=bangkok_geo,
    name="Bangkok Districts (bounds)",
    style_function=bounds_style,
    highlight_function=lambda f: {'weight':3.6, 'color':'#000000'},
    tooltip=GeoJsonTooltip(fields=[district_name_field], aliases=['เขต:'])
).add_to(districts_layer)

# -------------------------
# District popup on click (separate, dedicated popup layer)
# DEFAULT VISIBLE
# -------------------------
popup_fields = [district_name_field, 'num_hospitals', 'num_communities', 'sum_hospital_weights']
popup_aliases = ['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:', 'sum weights:']
popup = GeoJsonPopup(fields=popup_fields, aliases=popup_aliases, localize=True, labels=True, style="background-color: white;")
popup_layer = folium.FeatureGroup(name="District Popups (click)", show=True).add_to(m)
gj_popup = folium.GeoJson(data=bangkok_geo, popup=popup, name="District Popups").add_to(popup_layer)

# -------------------------
# Click-to-highlight + zoom JS bound directly to gj_bounds var created by folium
# This is more robust than scanning all layers.
# -------------------------
click_highlight_js = """
<script>
(function(){
  var map = %s;
  var gjVarName = '%s';
  var gj = window[gjVarName];
  console.log('click_highlight: gjVarName=', gjVarName, 'gj=', gj);
  var previous = null;

  function resetStyle(layer) {
    try { if (layer.setStyle) layer.setStyle({fillOpacity:0, fillColor:'transparent'}); } catch(e){}
  }

  function bindOnce() {
    try {
      if (!gj || !gj.eachLayer) return false;
      gj.eachLayer(function(layer){
        try {
          if (layer._hasClickHandler) return;
          layer.on('click', function(e){
            if (previous && previous !== layer) {
              resetStyle(previous);
              previous = null;
            }
            try { layer.setStyle({fillColor: '#2196F3', fillOpacity: 0.35}); previous = layer; } catch(err){}
            try {
              if (layer.getBounds) map.fitBounds(layer.getBounds(), {padding: [20,20]});
              else if (e && e.target && e.target.getBounds) map.fitBounds(e.target.getBounds(), {padding: [20,20]});
              // bring to front if available
              try { if (layer.bringToFront) layer.bringToFront(); } catch(e){}
            } catch(err){}
          });
          layer._hasClickHandler = true;
        } catch(err){
          console.warn('bindOnce: layer bind error', err);
        }
      });
      return true;
    } catch(e){
      console.warn('bindOnce: exception', e);
      return false;
    }
  }

  // Try binding now; retry if needed
  var attempts = 0;
  var maxAttempts = 40;
  var intervalMs = 300;
  var interval = setInterval(function(){
    attempts += 1;
    try {
      var ok = bindOnce();
      if (ok || attempts >= maxAttempts) {
        clearInterval(interval);
        console.log('click_highlight: bindOnce completed, ok=', ok, 'attempts=', attempts);
      }
    } catch(e){
      console.warn('click_highlight: attempt error', e);
    }
  }, intervalMs);

})();
</script>
""" % (m.get_name(), gj_bounds.get_name())

m.get_root().html.add_child(folium.Element(click_highlight_js))

# -------------------------
# Layer Control and Save
# -------------------------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Map saved as {OUT_HTML}")