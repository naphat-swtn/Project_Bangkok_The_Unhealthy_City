# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Population.py

Generate Ratchathewi_Hospital_Population.html — focused population map for
เขตราชเทวี only. Metrics (population served per hospital, assigned communities)
are computed globally (across Bangkok) like the BKK_Hospital_Population approach,
but the map displays only the ราชเทวี polygon and hospitals inside it.

Adjustments per request:
- Marker color/gradient uses red (same style as congestion red).
- Hospital popup shows:
    เขต
    จำนวนชุมชนใกล้เคียง
    จำนวนประชากรใกล้เคียงที่ต้องรองรับ
    จำนวนเตียง

Usage:
  python Ratchathewi_Hospital_Population.py
  python -m http.server 8000
  Open http://localhost:8000/Ratchathewi_Hospital_Population.html
"""
import json
import base64
from pathlib import Path
import html
import math
import sys

import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape, Point as ShapelyPoint
from geopy.distance import geodesic

# ---------- Config / paths ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "Ratchathewi_Hospital_Population.html"

HOSP_ICON_FN = "Hospital.png"

# center icon slightly reduced (match other pages)
ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"

# ---------- Helpers ----------
def try_inline_image(path):
    p = Path(path)
    if p.exists():
        b = p.read_bytes()
        ext = p.suffix.lower()
        mime = "image/png"
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".svg":
            mime = "image/svg+xml"
        return "data:{};base64,{}".format(mime, base64.b64encode(b).decode("ascii"))
    return path

def detect_name_field(features):
    if not features:
        return None
    props = features[0].get('properties', {}) or {}
    for candidate in ('amp_th', 'district', 'name', 'NAME', 'AMP_T', 'AMP_THA', 'DISTRICT'):
        if candidate in props:
            return candidate
    keys = list(props.keys())
    return keys[0] if keys else None

def pretty(s):
    return html.escape(str(s)) if s is not None else ''

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb):
    return '#{:02x}{:02x}{:02x}'.format(*[int(max(0, min(255, round(v)))) for v in rgb])

def mix_colors(hex1, hex2, t):
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    r = r1 + (r2 - r1) * t
    g = g1 + (g2 - g1) * t
    b = b1 + (b2 - b1) * t
    return rgb_to_hex((r, g, b))

# ---------- Load inputs ----------
for p in (HOSPITALS_CSV, COMMUNITIES_CSV, GEOJSON_PATH):
    if not Path(p).exists():
        raise SystemExit(f"Missing required file: {p}")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# ---------- Sanity / detect columns ----------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Expected hospital coords columns '{LAT_COL}', '{LON_COL}' in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Expected community coords columns '{LAT_COL}', '{LON_COL}' in {COMMUNITIES_CSV}")

possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# ensure numeric fields exist
beds_col = "จำนวนเตียง"
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# detect population column in communities
possible_pop_cols = ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร']
comm_pop_col = next((c for c in possible_pop_cols if c in communities.columns), None)
if comm_pop_col is None:
    communities['population'] = 0
    comm_pop_col = 'population'
else:
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Compute assignments and population metrics USING ALL DATA (global) ----------
# Assign each community to its nearest hospital among ALL hospitals (global assignment)
comm_assigned_global = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[LAT_COL]); comm_lon = float(comm[LON_COL])
    except Exception:
        comm_assigned_global.append((c_idx, None, None)); continue
    min_dist = float('inf'); nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            h_lat = float(hosp[LAT_COL]); h_lon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((comm_lat, comm_lon), (h_lat, h_lon)).meters
        if d < min_dist:
            min_dist = d; nearest_idx = h_idx
    comm_assigned_global.append((c_idx, nearest_idx, min_dist if min_dist != float('inf') else None))

# compute per-hospital population served and number of communities (global metrics)
h_metrics_global = {}
for h_idx, _ in hospitals.iterrows():
    h_metrics_global[h_idx] = {'num_communities': 0, 'sum_population': 0}

for c_idx, h_idx, d in comm_assigned_global:
    if h_idx is not None and pd.notnull(h_idx):
        pop = int(communities.loc[c_idx].get(comm_pop_col, 0) or 0)
        h_metrics_global.setdefault(h_idx, {'num_communities': 0, 'sum_population': 0})
        h_metrics_global[h_idx]['num_communities'] += 1
        h_metrics_global[h_idx]['sum_population'] += pop

# global maxima for normalization
global_max_pop = max((v['sum_population'] for v in h_metrics_global.values()), default=1)
global_max_comm = max((v['num_communities'] for v in h_metrics_global.values()), default=1)

# ---------- Compute district metrics globally (population per district) ----------
district_features = bangkok_geo.get('features', []) or []
district_name_field = detect_name_field(district_features) or 'amp_th'

# build shapes and names
district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field) if district_name_field else None
    district_names.append(name)
    district_shapes.append(shape(geom) if geom is not None else None)

district_metrics = {}
for name in district_names:
    district_metrics[name] = {'num_hospitals': 0, 'num_communities': 0, 'sum_population': 0}

# assign hospitals to districts
for h_idx, hosp in hospitals.iterrows():
    try:
        pt = ShapelyPoint(float(hosp[LON_COL]), float(hosp[LAT_COL]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None: continue
        try:
            if poly.contains(pt):
                name = district_names[i]
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_population': 0})
                district_metrics[name]['num_hospitals'] += 1
                break
        except Exception:
            continue

# assign communities to districts and sum populations (global)
for c_idx, comm in communities.iterrows():
    try:
        pt = ShapelyPoint(float(comm[LON_COL]), float(comm[LAT_COL]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None: continue
        try:
            if poly.contains(pt):
                name = district_names[i]
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_population': 0})
                district_metrics[name]['num_communities'] += 1
                try:
                    pop = int(comm.get(comm_pop_col, 0) or 0)
                except Exception:
                    pop = 0
                district_metrics[name]['sum_population'] += pop
                break
        except Exception:
            continue

global_max_district_pop = max((v['sum_population'] for v in district_metrics.values()), default=1)

# ---------- Find target district feature and shape ----------
target_feat = None
for feat in district_features:
    props = feat.get('properties') or {}
    val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
    if val == TARGET_DISTRICT_THAI:
        target_feat = feat
        break
if target_feat is None:
    for feat in district_features:
        props = feat.get('properties') or {}
        val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
        if val and val.lower() == TARGET_DISTRICT_THAI.lower():
            target_feat = feat
            break
if target_feat is None:
    raise SystemExit(f"Could not find district '{TARGET_DISTRICT_THAI}' in {GEOJSON_PATH}")

target_shape = shape(target_feat.get('geometry'))

# ---------- Select hospitals/communities inside the target district (for display only) ----------
hospitals_in = []
for h_idx, hosp in hospitals.iterrows():
    try:
        pt = ShapelyPoint(float(hosp[LON_COL]), float(hosp[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            hospitals_in.append((h_idx, hosp))
    except Exception:
        continue

communities_in = []
for c_idx, comm in communities.iterrows():
    try:
        pt = ShapelyPoint(float(comm[LON_COL]), float(comm[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            communities_in.append((c_idx, comm))
    except Exception:
        continue

# ---------- Prepare district feature for embedding (global metrics for that district) ----------
district_name = target_feat.get('properties', {}).get(district_name_field) or TARGET_DISTRICT_THAI
dm = district_metrics.get(district_name, {'num_hospitals': 0, 'num_communities': 0, 'sum_population': 0})
props = target_feat.get('properties', {}) or {}
props['district_name'] = district_name
props['amp_th'] = district_name
props['name'] = district_name
props['num_hospitals'] = int(dm.get('num_hospitals', 0))
props['num_communities'] = int(dm.get('num_communities', 0))
props['sum_population'] = int(dm.get('sum_population', 0))
props['choropleth_norm'] = float(props['sum_population']) / float(global_max_district_pop) if global_max_district_pop > 0 else 0.0
highlight_feature = {"type": "Feature", "geometry": target_feat.get('geometry'), "properties": props}

# ---------- Build map centered on district ----------
centroid = target_shape.centroid
center_point = [centroid.y, centroid.x]
m = folium.Map(location=center_point, zoom_start=15, tiles=None)

# base tiles
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed district (hidden from LayerControl)
districts_layer = FeatureGroup(name=f"{props['district_name']} (highlight)", show=True, control=False).add_to(m)
district_geo = {"type":"FeatureCollection","features":[highlight_feature]}
district_gj = folium.GeoJson(
    data=district_geo,
    style_function=lambda feat: {
        'fillColor': '#3388ff',
        'color': '#000000',
        'weight': 3.5,
        'fillOpacity': 0.22,
        'opacity': 0.95,
        'interactive': True
    },
    tooltip=GeoJsonTooltip(fields=['district_name','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_layer)

# ---------- Population markers (hospitals in district) — color/size by global population served ----------
# Use red color scale (like congestion) per request: small->large red
small_color_hex = '#ffdede'
large_color_hex = '#b71c1c'
min_radius = 6
max_radius = 36
max_pop_global = global_max_pop

population_layer = folium.FeatureGroup(name="Hospitals (by population served)", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)

for h_idx, hosp in hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    # global population served for this hospital
    sum_pop = h_metrics_global.get(h_idx, {}).get('sum_population', 0)
    num_c = h_metrics_global.get(h_idx, {}).get('num_communities', 0)
    normalized = (math.sqrt(sum_pop) / math.sqrt(max_pop_global)) if max_pop_global > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized

    title = hosp.get('โรงพยาบาล') or hosp.get(hosp_name_col) or ''
    title_esc = pretty(title)
    district_val = district_name
    beds = int(hosp.get(beds_col, 0) or 0)

    # Popup fields per request:
    # เขต / จำนวนชุมชนใกล้เคียง / จำนวนประชากรใกล้เคียงที่ต้องรองรับ / จำนวนเตียง
    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; max-width:420px;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px; font-size:14px;">
        <div><strong>เขต:</strong> {pretty(district_val)}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {num_c}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {sum_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """

    folium.CircleMarker(
        location=[latf, lonf],
        radius=radius,
        color=color_hex,
        fill=True,
        fill_color=color_hex,
        fill_opacity=fill_opacity,
        weight=stroke_weight,
        popup=folium.Popup(popup_html, max_width=420),
        tooltip=f"{title} — {sum_pop} คน"
    ).add_to(population_layer)

    # small center icon
    try:
        icon = folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon, tooltip=f"{title} (center)").add_to(population_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=7, color='#b71c1c', fill=True, fill_color='#b71c1c', fill_opacity=1.0, weight=0.6).add_to(population_layer)

# ---------- Optional: connections from communities_in to hospitals (only when assigned hospital is inside district) ----------
conn_layer = FeatureGroup(name="Connections (community → hospital)", show=False, control=False).add_to(m)
assigned_lookup = {c_idx: h_idx for c_idx, h_idx, d in comm_assigned_global}
for c_idx, comm in communities_in:
    assigned_h = assigned_lookup.get(c_idx)
    if assigned_h is None or pd.isna(assigned_h):
        continue
    # only draw connection if the assigned hospital is also inside the district
    if assigned_h in dict(hospitals_in).keys():
        hosp = hospitals.loc[assigned_h]
        try:
            clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        folium.PolyLine(locations=[[clat, clon], [hlat, hlon]], color='#9E9E9E', weight=1.0, opacity=0.5).add_to(conn_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label {
  font-family: 'Bai Jamjuree', sans-serif !important;
  font-size: 16px !important;
  line-height: 1.2 !important;
}
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: ensure district polygon behind markers and keep tooltip behavior (no hover recolor) ----------
district_var = district_gj.get_name()
map_var = m.get_name()
js_reorder_and_bind = f"""
<script>
(function(){{
  try {{
    var map = {map_var};
    var gj = {district_var};
    function reorder(){{
      try {{ if (gj && gj.bringToBack) gj.bringToBack(); }} catch(e){{ console.warn(e); }}
    }}
    setTimeout(reorder, 50); setTimeout(reorder, 300); setTimeout(reorder, 1000);

    if (gj && gj.eachLayer) {{
      gj.eachLayer(function(layer){{
        try {{
          layer.on('mouseover', function(e){{ try{{ this.openTooltip(e.latlng); }}catch(e){{}} }});
          layer.on('mouseout', function(e){{ try{{ this.closeTooltip(); }}catch(e){{}} }});
          layer.on('click', function(e){{
            try {{
              if (window._lastDistrict && window._lastDistrict !== this) {{
                try {{ window._lastDistrict.setStyle({{color: window._lastDistrict.origColor || '#000000', weight: window._lastDistrict.origWeight || 3.5, fillOpacity: window._lastDistrict.origFillOpacity || 0.22}}); }} catch(e){{}}
              }}
              if (!this.origColor) {{ this.origColor = this.options.color; this.origWeight = this.options.weight; this.origFillOpacity = this.options.fillOpacity; }}
              this.setStyle({{color:'#000000', weight:5, fillOpacity:0.45}});
              window._lastDistrict = this;
            }} catch(err){{ console.warn(err); }}
          }});
        }} catch(e){{ console.warn('bind err', e); }}
      }});
    }}
  }} catch(e){{ console.warn('init err', e); }}
}})();
</script>
"""
m.get_root().html.add_child(folium.Element(js_reorder_and_bind))

# ---------- LayerControl and save ----------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Saved: {OUT_HTML}")
print(f"Global hospitals: {len(hospitals)}, Global communities: {len(communities)}")
print(f"Hospitals in {TARGET_DISTRICT_THAI}: {len(hospitals_in)}, Communities in {TARGET_DISTRICT_THAI}: {len(communities_in)}")