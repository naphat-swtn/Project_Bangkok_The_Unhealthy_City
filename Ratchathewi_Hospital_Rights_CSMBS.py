# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Rights_CSMBS.py

Generate Ratchathewi_Hospital_Rights_CSMBS.html — focused view showing hospitals that
accept สิทธิข้าราชการ (CSMBS) located inside เขตราชเทวี.

Behavior:
- Loads hospitals.csv, communities.csv, districts_bangkok.geojson.
- Computes community -> nearest-hospital assignment (global) to populate hospital weight.
- Computes district metrics globally (so tooltip numbers are consistent with BKK pages).
- Finds the ราชเทวี polygon and embeds only that district (highlight).
- Shows only hospitals that accept สิทธิข้าราชการ (CSMBS) per detected column and are located in ราชเทวี.
- Marker uses Hospital_CSMBS.png if present (relative filename) else falls back to a green circle.
- Popup shows:
    - ชื่อโรงพยาบาล
    - เขต
    - เบอร์
    - เว็บไซต์
    - สิทธิบัตรทอง: Yes/No
    - สิทธิประกันสังคม: Yes/No
    - สิทธิข้าราชการ: Yes/No
"""
import json
from pathlib import Path
import html
import sys
import base64

import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape
from shapely.geometry import Point as ShapelyPoint
from geopy.distance import geodesic

# ---------- Config ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "Ratchathewi_Hospital_Rights_CSMBS.html"

HOSP_ICON_FN = "Hospital.png"         # small inline icon for popup header (if present)
CSMBS_ICON_FN = "Hospital_CSMBS.png"  # marker icon for CSMBS hospitals (relative path preferred)
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

def detect_rights_column(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    lc = {col.lower(): col for col in cols}
    for c in candidates:
        if c.lower() in lc:
            return lc[c.lower()]
    return None

def truthy(val):
    if pd.isna(val):
        return False
    s = str(val).strip().lower()
    if s in ('1','y','yes','true','รับ','ใช่','t','on'):
        return True
    try:
        return float(s) > 0
    except Exception:
        return False

def yesno_from_row(row, col):
    return "Yes" if truthy(row.get(col, "")) else "No"

def esc(s):
    return html.escape(str(s)) if s is not None else ''

# ---------- Load data ----------
for p in (HOSPITALS_CSV, COMMUNITIES_CSV, GEOJSON_PATH):
    if not Path(p).exists():
        raise SystemExit(f"Missing required file: {p}")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# ---------- Sanity checks ----------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Hospital coords columns '{LAT_COL}'/'{LON_COL}' not found in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Community coords columns '{LAT_COL}'/'{LON_COL}' not found in {COMMUNITIES_CSV}")

possible_hosp_name_cols = ['โรงพยาบาล','โรงพาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# detect rights columns (include common variants and exact Thai names)
gold_candidates = ['สิทธิบัตรทอง', 'รับสิทธิบัตรทอง', 'UHC', 'gold_card', 'รับ_uc', 'รับสิทธิ']
sso_candidates = ['สิทธิประกันสังคม', 'ประกันสังคม', 'SSS', 'social_security', 'รับ_sss']
gov_candidates = ['สิทธิข้าราชการ', 'ข้าราชการ', 'CSMBS', 'csmbs', 'รับข้าราชการ']

gold_col = detect_rights_column(hospitals.columns, gold_candidates)
sso_col = detect_rights_column(hospitals.columns, sso_candidates)
gov_col = detect_rights_column(hospitals.columns, gov_candidates)

# ensure columns exist (create empty if missing)
if gold_col is None:
    hospitals['สิทธิบัตรทอง'] = ""
    gold_col = 'สิทธิบัตรทอง'
if sso_col is None:
    hospitals['สิทธิประกันสังคม'] = ""
    sso_col = 'สิทธิประกันสังคม'
if gov_col is None:
    hospitals['สิทธิข้าราชการ'] = ""
    gov_col = 'สิทธิข้าราชการ'

# ensure numeric popup fields exist
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Compute community -> nearest hospital assignment (global) ----------
comm_assigned = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[LAT_COL]); comm_lon = float(comm[LON_COL])
    except Exception:
        comm_assigned.append((c_idx, None, None)); continue
    min_d = float('inf'); nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            h_lat = float(hosp[LAT_COL]); h_lon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((comm_lat, comm_lon), (h_lat, h_lon)).meters
        if d < min_d:
            min_d = d; nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_d if min_d != float('inf') else None))

hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Compute district metrics globally (for tooltips) ----------
district_features = bangkok_geo.get('features', []) or []
district_name_field = None
if district_features:
    props0 = district_features[0].get('properties', {}) or {}
    for candidate in ('amp_th','district','name','NAME','AMP_T','AMP_THA','DISTRICT'):
        if candidate in props0:
            district_name_field = candidate
            break
if district_name_field is None:
    district_name_field = 'amp_th'

district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field)
    district_names.append(name)
    district_shapes.append(shape(geom) if geom is not None else None)

district_metrics = {name: {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0} for name in district_names}

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

max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

# inject metrics into geojson features
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / max_sum_weights) if max_sum_weights > 0 else 0.0

# ---------- Find Ratchathewi feature ----------
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

# ---------- Hospitals in target district ----------
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

# filter to CSMBS-accepting hospitals (truthy on gov_col)
csmbs_hospitals_in = []
for h_idx, hosp in hospitals_in:
    if truthy(hosp.get(gov_col, "")):
        csmbs_hospitals_in.append((h_idx, hosp))

# ---------- Prepare highlight feature for the district (use injected properties) ----------
props = target_feat.get('properties', {}) or {}
props['district_name'] = props.get(district_name_field) or props.get('name') or TARGET_DISTRICT_THAI
props['amp_th'] = props['district_name']
props['name'] = props['district_name']
highlight_feature = {"type":"Feature","geometry": target_feat.get('geometry'), "properties": props}
district_geo = {"type":"FeatureCollection","features":[highlight_feature]}

# ---------- Icons ----------
CSMBS_ICON_URI = try_file_name(CSMBS_ICON_FN)
HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)

# ---------- Build folium map centered on district ----------
centroid = list(target_shape.centroid.coords)[0]  # (x, y)
center = [centroid[1], centroid[0]]
m = folium.Map(location=center, zoom_start=15, tiles=None)

# base tiles (Thai)
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

# ---------- CSMBS hospitals layer (only in district) ----------
csmbs_layer = FeatureGroup(name="Hospitals - สิทธิข้าราชการ (CSMBS)", show=True, control=False).add_to(m)

for h_idx, hosp in csmbs_hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    district_val = hosp.get('เขต') or hosp.get('district') or props.get('district_name') or ''
    tel_val = hosp.get('tel') or hosp.get('โทรศัพท์') or ''
    url_val = hosp.get('url') or hosp.get('website') or ''

    gold_v = yesno_from_row(hosp, gold_col)
    sso_v = yesno_from_row(hosp, sso_col)
    gov_v = yesno_from_row(hosp, gov_col)

    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; max-width:480px;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px; font-size:14px; line-height:1.35;">
        <div><strong>เขต:</strong> {esc(district_val)}</div>
        <div><strong>เบอร์:</strong> {esc(tel_val)}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{esc(url_val)}" target="_blank" rel="noopener noreferrer">{esc(url_val)}</a></div>
        <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
        <div><strong>สิทธิบัตรทอง:</strong> {gold_v}</div>
        <div><strong>สิทธิประกันสังคม:</strong> {sso_v}</div>
        <div><strong>สิทธิข้าราชการ:</strong> {gov_v}</div>
      </div>
    </div>
    """

    try:
        icon = folium.CustomIcon(CSMBS_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon,
                      popup=folium.Popup(popup_html, max_width=480),
                      tooltip=title_esc).add_to(csmbs_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#4caf50', fill=True, fill_color='#4caf50',
                            popup=folium.Popup(popup_html, max_width=480), tooltip=title_esc).add_to(csmbs_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size:16px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: bind district tooltip/click behaviors (no hover recolor) ----------
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
              if (this.getBounds) map.fitBounds(this.getBounds(), {{padding:[20,20]}});
            }} catch(err){{ console.warn(err); }}
          }});
        }} catch(e){{ console.warn('bind layer err', e); }}
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
print(f"Hospitals in Ratchathewi accepting CSMBS: {len(csmbs_hospitals_in)}")