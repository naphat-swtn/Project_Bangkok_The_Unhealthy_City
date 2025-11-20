# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_BedNumber.py

Generate Ratchathewi_Hospital_BedNumber.html — a focused "beds" map for เขตราชเทวี.
(Verified to use consistent loop variable names to avoid NameError: 'h' is not defined.)
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

# --- Config ---
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "Ratchathewi_Hospital_BedNumber.html"

HOSP_ICON_FN = "Hospital.png"
ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"

# --- Helpers ---
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

# --- Load inputs ---
for p in (HOSPITALS_CSV, COMMUNITIES_CSV, GEOJSON_PATH):
    if not Path(p).exists():
        raise SystemExit(f"Missing required file: {p}")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# --- Sanity / detect columns ---
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Expected hospital coords columns '{LAT_COL}', '{LON_COL}' in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Expected community coords columns '{LAT_COL}', '{LON_COL}' in {COMMUNITIES_CSV}")

possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# ensure numeric popup fields exist
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# detect community population column (optional)
possible_pop_cols = ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร']
comm_pop_col = next((c for c in possible_pop_cols if c in communities.columns), None)
if comm_pop_col is None:
    communities['population'] = 0
    comm_pop_col = 'population'
else:
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col, 0), errors='coerce').fillna(0).astype(int)

# --- Compute assignments and hospital weights GLOBALLY ---
comm_assigned_global = []  # (c_idx, nearest_h_idx or None, dist_m)
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

# hospital weight = number of communities assigned (global)
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned_global:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# --- Compute district metrics globally ---
district_features = bangkok_geo.get('features', []) or []
district_name_field = detect_name_field(district_features) or 'amp_th'

# build shapes & names
district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field) if district_name_field else None
    district_names.append(name)
    district_shapes.append(shape(geom) if geom is not None else None)

district_metrics = {name: {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0} for name in district_names}

# assign hospitals to districts (global)
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
                m = district_metrics.setdefault(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                m['num_hospitals'] += 1
                m['sum_hospital_weights'] += int(hosp.get('weight',0) or 0)
                break
        except Exception:
            continue

# assign communities to districts (global)
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
                m = district_metrics.setdefault(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                m['num_communities'] += 1
                break
        except Exception:
            continue

global_max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

# --- Find target district feature and shape ---
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

# --- Select hospitals/communities inside the target district (display only) ---
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

# --- Prepare embedded district feature (use global metrics) ---
district_name = target_feat.get('properties', {}).get(district_name_field) or TARGET_DISTRICT_THAI
dm = district_metrics.get(district_name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
props = target_feat.get('properties', {}) or {}
props['district_name'] = district_name
props['amp_th'] = district_name
props['name'] = district_name
props['num_hospitals'] = int(dm.get('num_hospitals',0))
props['num_communities'] = int(dm.get('num_communities',0))
props['sum_hospital_weights'] = int(dm.get('sum_hospital_weights',0))
props['choropleth_norm'] = float(props['sum_hospital_weights']) / float(global_max_sum_weights) if global_max_sum_weights>0 else 0.0
highlight_feature = {"type":"Feature","geometry":target_feat.get('geometry'), "properties":props}

# --- Map build ---
centroid = target_shape.centroid
center_point = [centroid.y, centroid.x]
m = folium.Map(location=center_point, zoom_start=15, tiles=None)

# base tiles (Thai)
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed district
districts_fg = FeatureGroup(name=f"{props['district_name']} (highlight)", show=True, control=False).add_to(m)
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
    tooltip=GeoJsonTooltip(fields=['amp_th','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_fg)

# --- Beds-based markers (use global max beds for normalization) ---
small_color_hex = '#ffdede'
large_color_hex = '#b71c1c'
min_radius = 6
max_radius = 36
max_beds_global = hospitals[beds_col].max() if len(hospitals)>0 else 0

beds_layer = FeatureGroup(name="Hospitals (by beds) - marker size", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)

for h_idx, hosp in hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    beds_val = int(hosp.get(beds_col, 0) or 0)
    normalized = (math.sqrt(beds_val) / math.sqrt(max_beds_global)) if max_beds_global > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized

    # metrics for popup (use global assignment results)
    num_comm = hosp.get('weight', 0)
    near_pop = int(hosp.get(near_pop_col, 0) or 0)
    beds_num = beds_val
    title = hosp.get('โรงพยาบาล') or hosp.get(hosp_name_col) or ''
    title_esc = pretty(title)
    district_val = district_name

    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; max-width:380px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {pretty(district_val)}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {int(num_comm)}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds_num}</div>
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
        tooltip=f"{title} — {beds_num} เตียง"
    ).add_to(beds_layer)

    # small center icon
    try:
        icon = folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon, tooltip=title_esc).add_to(beds_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=7, color='#d32f2f', fill=True, fill_color='#d32f2f', fill_opacity=1.0, weight=0.6).add_to(beds_layer)

# --- Optional: connections from communities_in to hospitals (only when assigned hospital is inside district) ---
conn_layer = FeatureGroup(name="Connections (community → hospital)", show=False, control=False).add_to(m)
assigned_lookup = {c_idx: h_idx for c_idx, h_idx, d in comm_assigned_global}
for c_idx, comm in communities_in:
    assigned_h = assigned_lookup.get(c_idx)
    if assigned_h is None or pd.isna(assigned_h):
        continue
    if assigned_h in dict(hospitals_in).keys():
        hosp = hospitals.loc[assigned_h]
        try:
            clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        folium.PolyLine(locations=[[clat, clon],[hlat, hlon]], color='#9E9E9E', weight=1.0, opacity=0.6).add_to(conn_layer)

# --- CSS ---
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

# --- JS: ensure district polygon behind markers and bind tooltip/click (no hover recolor) ---
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

# --- LayerControl and save ---
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Saved: {OUT_HTML}")
print(f"Global hospitals: {len(hospitals)}, Global communities: {len(communities)}")
print(f"Hospitals in {TARGET_DISTRICT_THAI}: {len(hospitals_in)}, Communities in {TARGET_DISTRICT_THAI}: {len(communities_in)}")