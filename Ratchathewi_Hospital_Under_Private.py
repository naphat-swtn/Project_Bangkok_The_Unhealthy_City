# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Under_Private.py

Generate Ratchathewi_Hospital_Under_Private.html — focused "private hospitals" view for
เขตราชเทวี.

Behavior:
- Loads hospitals.csv, communities.csv, districts_bangkok.geojson.
- Computes community -> nearest-hospital assignment globally (weights computed but not shown).
- Computes district metrics globally (so district tooltip numbers match the BKK pages).
- Finds the ราชเทวี polygon and embeds only that district (highlight).
- Shows only private hospitals (ประเภท == "เอกชน") that lie inside ราชเทวี.
- Private markers use Hospital_Private.png if present (relative filename); fallback to pink circle.
- Popups show: ชื่อโรงพยาบาล, เขต, เบอร์, เว็บไซต์, ประเภท.
- District tooltip shows เขต / จำนวนโรงพยาบาล / จำนวนชุมชน (using global metrics).
- Mobile tap-to-toggle tooltips and click-to-highlight for the district polygon included.
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
from shapely.geometry import shape
from shapely.geometry import Point as ShapelyPoint
from geopy.distance import geodesic

# ---------- Config / paths ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "Ratchathewi_Hospital_Under_Private.html"

HOSP_ICON_FN = "Hospital.png"            # in-popup small icon (inline if present)
PRIVATE_ICON_FN = "Hospital_Private.png" # marker icon for private hospitals (relative preferred)
PUSH_PIN_FN = "RoundPushpin.png"

ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"

# ---------- Helpers ----------
def try_file_name(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

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
    for candidate in ('amp_th','district','name','NAME','AMP_T','AMP_THA','DISTRICT'):
        if candidate in props:
            return candidate
    keys = list(props.keys())
    return keys[0] if keys else None

def esc(s):
    return html.escape(str(s)) if s is not None else ''

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

possible_hosp_name_cols = ['โรงพยาบาล','โรงพาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# ensure 'ประเภท' exists and normalized
type_col = "ประเภท"
if type_col not in hospitals.columns:
    hospitals[type_col] = ""
else:
    hospitals[type_col] = hospitals[type_col].astype(str).str.strip()

# ensure numeric fields exist (weights / pop / beds kept but not shown)
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Compute global community -> nearest hospital assignment (weights) ----------
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

hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned_global:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Compute district metrics globally ----------
district_features = bangkok_geo.get('features', []) or []
district_name_field = detect_name_field(district_features) or 'amp_th'

district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field)
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
                m['sum_hospital_weights'] += int(hosp.get('weight', 0) or 0)
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

# inject metrics into geojson features so tooltips show numbers
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / global_max_sum_weights) if global_max_sum_weights > 0 else 0.0

# ---------- Find target district feature and shape ----------
target_feat = None
for feat in district_features:
    props = feat.get('properties', {}) or {}
    val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
    if val == TARGET_DISTRICT_THAI:
        target_feat = feat
        break
if target_feat is None:
    for feat in district_features:
        props = feat.get('properties', {}) or {}
        val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
        if val and val.lower() == TARGET_DISTRICT_THAI.lower():
            target_feat = feat
            break
if target_feat is None:
    raise SystemExit(f"Could not find district '{TARGET_DISTRICT_THAI}' in {GEOJSON_PATH}")

target_shape = shape(target_feat.get('geometry'))

# ---------- Select hospitals inside the target district (display only) ----------
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

# filter to private hospitals (ประเภท == "เอกชน")
priv_hospitals_in = []
for h_idx, hosp in hospitals_in:
    if (str(hosp.get(type_col, "")).strip() == "เอกชน"):
        priv_hospitals_in.append((h_idx, hosp))

# ---------- Prepare highlight feature for the district (use injected properties) ----------
props = target_feat.get('properties', {}) or {}
props['district_name'] = props.get(district_name_field) or props.get('name') or TARGET_DISTRICT_THAI
props['amp_th'] = props['district_name']
props['name'] = props['district_name']
highlight_feature = {"type":"Feature","geometry": target_feat.get('geometry'), "properties": props}
district_geo = {"type":"FeatureCollection","features":[highlight_feature]}

# ---------- Icons ----------
PRIV_ICON_URI = try_file_name(PRIVATE_ICON_FN)
HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)

# ---------- Build folium map centered on district ----------
centroid = target_shape.centroid
center_point = [centroid.y, centroid.x]
m = folium.Map(location=center_point, zoom_start=15, tiles=None)

# base tiles
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed only target district (hidden from LayerControl)
districts_fg = FeatureGroup(name=f"{props['district_name']} (highlight)", show=True, control=False).add_to(m)
district_gj = folium.GeoJson(
    data=district_geo,
    style_function=lambda feat: {
        'fillColor': '#3388ff',
        'color': '#000000',
        'weight': 3.5,
        'fillOpacity': 0.25,
        'opacity': 0.95,
        'interactive': True
    },
    tooltip=GeoJsonTooltip(fields=['amp_th','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_fg)

# ---------- Private hospitals layer (only in district) ----------
priv_layer = FeatureGroup(name="Hospitals - เอกชน (private only)", show=True, control=False).add_to(m)

for h_idx, hosp in priv_hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    district_val = hosp.get('เขต') or hosp.get('district') or props.get('district_name') or ''
    tel_val = hosp.get('tel') or hosp.get('โทรศัพท์') or ''
    url_val = hosp.get('url') or hosp.get('website') or ''

    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; max-width:420px;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px; font-size:14px;">
        <div><strong>เขต:</strong> {esc(district_val)}</div>
        <div><strong>เบอร์:</strong> {esc(tel_val)}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{esc(url_val)}" target="_blank" rel="noopener noreferrer">{esc(url_val)}</a></div>
        <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
        <div><strong>ประเภท:</strong> {esc('เอกชน')}</div>
      </div>
    </div>
    """

    try:
        icon = folium.CustomIcon(PRIV_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon,
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title_esc).add_to(priv_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#ff80b3', fill=True, fill_color='#ff80b3',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(priv_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label {
  font-family: 'Bai Jamjuree', sans-serif !important;
  font-size: 16px !important;
}
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: ensure district polygon behind markers and bind tooltip/click (no hover recolor) ----------
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
                try {{ window._lastDistrict.setStyle({{color: window._lastDistrict.origColor || '#000000', weight: window._lastDistrict.origWeight || 3.5, fillOpacity: window._lastDistrict.origFillOpacity || 0.25}}); }} catch(e){{}}
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
print(f"Private hospitals in {TARGET_DISTRICT_THAI}: {len(priv_hospitals_in)}")