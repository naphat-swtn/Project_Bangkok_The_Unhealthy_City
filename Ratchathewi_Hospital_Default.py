# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Default.py

Generate Ratchathewi_Hospital_Default.html — a focused map showing only data inside
เขตราชเทวี (Ratchathewi). Adapted from BKK_Hospital_Distance_Default.py with these changes:
- When hovering the district, DO NOT change the district color (no visual hover highlight).
- Do NOT show community markers and do NOT draw connection lines.
- Hospital popups show: ชื่อโรงพยาบาล / เขต / เบอร์ / เว็บไซต์
- District tooltip shows number of hospitals and number of communities in the district.

Usage:
  python Ratchathewi_Hospital_Default.py
  python -m http.server 8000
  Open: http://localhost:8000/Ratchathewi_Hospital_Default.html
"""
import json
from pathlib import Path
import html
import sys
import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape, Point as ShapelyPoint
from geopy.distance import geodesic

# ---------- Config ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
DISTRICTS_SRC = "districts.geojson"   # read-only
OUT_HTML = "Ratchathewi_Hospital_Default.html"
HOSP_ICON_FN = "Hospital.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"   # match this value in district properties (amp_th / name / similar)

# ---------- Helpers ----------
def try_file_name(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

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

# ---------- Load CSVs ----------
if not Path(HOSPITALS_CSV).exists():
    raise SystemExit("Missing hospitals.csv in the working directory.")
if not Path(COMMUNITIES_CSV).exists():
    # communities file is optional for this focused page (we won't display them),
    # but the original flow expects it; exit with clear message.
    raise SystemExit("Missing communities.csv in the working directory.")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Hospital coords columns '{LAT_COL}'/'{LON_COL}' not found in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Community coords columns '{LAT_COL}'/'{LON_COL}' not found in {COMMUNITIES_CSV}")

# detect name columns
possible_hosp_name = ['โรงพยาบาล','โรงพาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name if c in hospitals.columns), hospitals.columns[0])
# phone and website detection (for hospital popup)
possible_tel = ['tel','โทรศัพท์','phone','โทร']
possible_url = ['url','website','site','link']
tel_col = next((c for c in possible_tel if c in hospitals.columns), None)
url_col = next((c for c in possible_url if c in hospitals.columns), None)

# ---------- Read districts.geojson and find Ratchathewi polygon ----------
dist_path = Path(DISTRICTS_SRC)
if not dist_path.exists():
    raise SystemExit(f"{DISTRICTS_SRC} not found - please add the file to working dir.")

with dist_path.open('r', encoding='utf-8') as f:
    districts_gj = json.load(f)

district_features = districts_gj.get('features', []) or []
district_name_field = detect_name_field(district_features)

# find the target district feature (match Thai name)
target_feat = None
for feat in district_features:
    props = feat.get('properties') or {}
    name_val = str(props.get(district_name_field) or props.get('district_name') or props.get('name') or '').strip()
    if name_val == TARGET_DISTRICT_THAI:
        target_feat = feat
        break
# fallback: case-insensitive
if target_feat is None:
    for feat in district_features:
        props = feat.get('properties') or {}
        name_val = str(props.get(district_name_field) or props.get('district_name') or props.get('name') or '').strip()
        if name_val and name_val.lower() == TARGET_DISTRICT_THAI.lower():
            target_feat = feat
            break

if target_feat is None:
    raise SystemExit(f"Could not find district feature matching '{TARGET_DISTRICT_THAI}' in {DISTRICTS_SRC}. Check property names/values.")

target_shape = shape(target_feat.get('geometry'))

# ---------- Filter hospitals to those inside Ratchathewi ----------
hospitals_in = []
for h_idx, h in hospitals.iterrows():
    try:
        pt = ShapelyPoint(float(h[LON_COL]), float(h[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            hospitals_in.append((h_idx, h))
    except Exception:
        continue

# ---------- Filter communities inside Ratchathewi (for accurate tooltip count) ----------
communities_in = []
for c_idx, c in communities.iterrows():
    try:
        pt = ShapelyPoint(float(c[LON_COL]), float(c[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            communities_in.append((c_idx, c))
    except Exception:
        continue

# ---------- Compute nearest hospital among hospitals_in for each community_in (weights if needed) ----------
comm_assigned = []
for c_idx, comm in communities_in:
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        comm_assigned.append((c_idx, None, None)); continue
    min_dist = float('inf'); nearest_idx = None
    for h_idx, hosp in hospitals_in:
        try:
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((clat, clon), (hlat, hlon)).meters
        if d < min_dist:
            min_dist = d; nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_dist if min_dist != float('inf') else None))

# compute hospital weights (number of communities assigned) limited to hospitals_in
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Build modified geojson: only include the target district (for embedding) ----------
props = target_feat.get('properties', {}) or {}
props['district_name'] = props.get(district_name_field) or props.get('district_name') or props.get('name') or TARGET_DISTRICT_THAI
props['amp_th'] = props['district_name']
props['name'] = props['district_name']
# metrics for the district (now using actual counts)
props['num_hospitals'] = int(len(hospitals_in))
props['num_communities'] = int(len(communities_in))
props['sum_hospital_weights'] = int(sum(int(hospitals.at[idx, 'weight']) if idx in hospitals.index else 0 for idx, _ in hospitals_in))
props['choropleth_norm'] = 1.0  # single district -> max
highlight_feature = {"type": "Feature", "geometry": target_feat.get('geometry'), "properties": props}

# ---------- Build folium map centered on district centroid ----------
centroid = target_shape.centroid
center_point = [centroid.y, centroid.x]
m = folium.Map(location=center_point, zoom_start=15, tiles=None)

# base tiles
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# districts layer (only the target district), hidden from LayerControl
districts_fg = FeatureGroup(name=f"{props['district_name']} (highlight)", show=True, control=False).add_to(m)
district_geo = {"type":"FeatureCollection","features":[highlight_feature]}

# prominent style for target district
# NOTE: highlight_function removed (no color change on hover)
district_gj = folium.GeoJson(
    data=district_geo,
    name="Ratchathewi Highlight",
    style_function=lambda feat: {
        'fillColor': '#3388ff',
        'color': '#000000',
        'weight': 3.5,
        'fillOpacity': 0.25,
        'opacity': 0.95,
        'interactive': True
    },
    # do NOT provide highlight_function so hovering won't change color
    tooltip=GeoJsonTooltip(fields=['amp_th','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_fg)

# ---------- Hospitals layer (only hospitals_in) ----------
hosp_layer = FeatureGroup(name="Hospitals (Ratchathewi)", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
ICON_SIZE = (22,22); ICON_ANCHOR = (11,11)
for h_idx, hosp in hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = pretty(title)
    district_val = props['district_name']
    # phone & website (best-effort detection)
    tel_val = hosp.get('tel') or hosp.get('โทรศัพท์') or hosp.get('phone') or hosp.get('โทร') or ''
    url_val = hosp.get('url') or hosp.get('website') or hosp.get('site') or ''
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:380px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:18px;height:18px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {pretty(district_val)}</div>
        <div><strong>เบอร์:</strong> {pretty(tel_val)}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{html.escape(str(url_val))}" target="_blank" rel="noopener noreferrer">{html.escape(str(url_val))}</a></div>
      </div>
    </div>
    """
    try:
        folium.Marker(location=[latf, lonf], icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(hosp_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#d32f2f', fill=True, fill_color='#d32f2f',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(hosp_layer)

# Note: communities and connection lines are intentionally NOT added per request.

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:15px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: ensure district polygon is behind markers and bind tooltip/click events ----------
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
    setTimeout(reorder, 50);
    setTimeout(reorder, 300);
    setTimeout(reorder, 1000);

    // bind tooltip open/close and click-to-highlight but do NOT change color on hover
    if (gj && gj.eachLayer) {{
      gj.eachLayer(function(layer){{
        try {{
          // keep tooltip behavior
          layer.on('mouseover', function(e){{ try{{ this.openTooltip(e.latlng); }}catch(e){{}} }});
          layer.on('mouseout', function(e){{ try{{ this.closeTooltip(); }}catch(e){{}} }});
          // click: persistent thicker stroke (we allow click highlight)
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
print("Saved:", OUT_HTML)
print(f"Hospitals in Ratchathewi: {len(hospitals_in)}, Communities in Ratchathewi: {len(communities_in)}")