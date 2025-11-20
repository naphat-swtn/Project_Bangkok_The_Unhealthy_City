# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Distance_CSMBS.py

Generate Ratchathewi_Hospital_Distance_CSMBS.html — focused CSMBS-distance map for
เขตราชเทวี.

Behavior:
- Detect hospitals that accept สิทธิข้าราชการ (CSMBS) and compute nearest-CSMBS assignment
  for every community (global assignment among CSMBS hospitals).
- Show:
  - CSMBS hospitals located inside ราชเทวี (marker + popup).
  - Communities that are inside ราชเทวี OR are outside but assigned to a CSMBS hospital
    that is inside ราชเทวี (markers in a distinct color).
  - Gray connection lines from those communities to their assigned CSMBS hospital.
  - CSMBS hospitals outside ราชเทวี that are linked from at least one community inside ราชเทวี
    (shown in a separate "Linked Hospitals (outside ราชเทวี)" layer).
- District polygon for ราชเทวี is embedded (tooltip + click-to-highlight) and moved behind markers.
- Tooltips for linked outside hospitals show only the hospital name (no "linked to..." text).
- If Hospital.png exists, it will be used as the marker icon for linked outside hospitals.
- Output: Ratchathewi_Hospital_Distance_CSMBS.html
"""
import json
from pathlib import Path
import html
import base64
import math

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
DISTRICTS_SRC = "districts_bangkok.geojson"
OUT_HTML = "Ratchathewi_Hospital_Distance_CSMBS.html"

HOSP_ICON_FN = "Hospital.png"
HOUSE_ICON_FN = "House.png"
CSMBS_ICON_FN = "Hospital_CSMBS.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"

# visual choices
CSMBS_LINE_COLOR = "#9E9E9E"        # gray for connection lines
CSMBS_COMM_COLOR = "#3949ab"        # indigo for community markers
CSMBS_HOSPITAL_COLOR = "#c62828"    # red fallback for hospitals without icon

ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

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

def detect_csmbs_column(df_cols):
    candidates = ['สิทธิข้าราชการ', 'ข้าราชการ', 'CSMBS', 'csmbs', 'รับข้าราชการ', 'รับ_csmbs', 'gov']
    for c in candidates:
        if c in df_cols:
            return c
    lc = {col.lower(): col for col in df_cols}
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

# ---------- Load data ----------
for p in (HOSPITALS_CSV, COMMUNITIES_CSV, DISTRICTS_SRC):
    if not Path(p).exists():
        raise SystemExit(f"Missing required file: {p}")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

with open(DISTRICTS_SRC, "r", encoding="utf-8") as f:
    districts_gj = json.load(f)

# ---------- Sanity ----------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Hospital coords columns '{LAT_COL}'/'{LON_COL}' not found in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Community coords columns '{LAT_COL}'/'{LON_COL}' not found in {COMMUNITIES_CSV}")

possible_hosp_name = ['โรงพยาบาล','โรงพาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name if c in hospitals.columns), hospitals.columns[0])
possible_comm_name = ['ชุมชน','ชื่อชุมชน','community','name','ชื่อ']
comm_name_col = next((c for c in possible_comm_name if c in communities.columns), communities.columns[0])

# community population col
possible_pop_cols = ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร']
comm_pop_col = next((c for c in possible_pop_cols if c in communities.columns), None)
if comm_pop_col is None:
    communities['population'] = 0
    comm_pop_col = 'population'
else:
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col,0), errors='coerce').fillna(0).astype(int)

# ensure hospital numeric popup fields exist
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col,0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col,0), errors='coerce').fillna(0).astype(int)

# ---------- Detect CSMBS hospitals ----------
csmbs_col = detect_csmbs_column(hospitals.columns)
if csmbs_col:
    hospitals['csmbs_accept'] = hospitals[csmbs_col].apply(truthy)
else:
    hospitals['csmbs_accept'] = False
    for col in hospitals.columns:
        if col.lower() in ('note','notes','type','remark','comment'):
            hospitals['csmbs_accept'] = hospitals['csmbs_accept'] | hospitals[col].astype(str).str.contains('ข้าราชการ|CSMBS|csmbs', case=False, na=False)

csmbs_hospitals = hospitals[hospitals['csmbs_accept'] == True].copy()
print(f"Detected CSMBS column: {csmbs_col}; CSMBS hospitals found: {len(csmbs_hospitals)}")

# ---------- Build district shapes ----------
district_features = districts_gj.get('features', []) or []
district_name_field = detect_name_field(district_features) or 'amp_th'

district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field)
    district_names.append(name)
    if geom:
        try:
            district_shapes.append(shape(geom))
        except Exception:
            district_shapes.append(None)
    else:
        district_shapes.append(None)

# ---------- Nearest-CSMBS hospital for each community (global among CSMBS hospitals) ----------
comm_assigned_csmbs = []
for c_idx, comm in communities.iterrows():
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        comm_assigned_csmbs.append((c_idx, None, None)); continue
    min_d = float('inf'); nearest = None
    for h_idx, hosp in csmbs_hospitals.iterrows():
        try:
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((clat, clon), (hlat, hlon)).meters
        if d < min_d:
            min_d = d; nearest = h_idx
    comm_assigned_csmbs.append((c_idx, nearest, min_d if min_d != float('inf') else None))

# compute CSMBS hospital weights (num communities assigned)
csmbs_hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned_csmbs:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            csmbs_hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Find Ratchathewi polygon ----------
target_feat = None
for feat in district_features:
    props = feat.get('properties') or {}
    nm = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
    if nm == TARGET_DISTRICT_THAI:
        target_feat = feat
        break
if target_feat is None:
    for feat in district_features:
        props = feat.get('properties') or {}
        nm = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
        if nm and nm.lower() == TARGET_DISTRICT_THAI.lower():
            target_feat = feat
            break
if target_feat is None:
    raise SystemExit(f"Could not find district '{TARGET_DISTRICT_THAI}' in {DISTRICTS_SRC}")

target_shape = shape(target_feat.get('geometry'))

# ---------- CSMBS hospitals inside ราชเทวี (displayed) ----------
csmbs_hospitals_in = []
h_in_set = set()
for h_idx, hosp in csmbs_hospitals.iterrows():
    try:
        pt = ShapelyPoint(float(hosp[LON_COL]), float(hosp[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            csmbs_hospitals_in.append((h_idx, hosp))
            h_in_set.add(h_idx)
    except Exception:
        continue

# ---------- Communities to show:
# - communities inside ราชเทวี OR
# - communities (outside) whose assigned nearest CSMBS hospital is inside ราชเทวี
comm_to_show = []
comm_to_show_set = set()
for c_idx, h_idx, d in comm_assigned_csmbs:
    try:
        comm_pt = ShapelyPoint(float(communities.loc[c_idx][LON_COL]), float(communities.loc[c_idx][LAT_COL]))
    except Exception:
        continue
    inside = False
    try:
        if target_shape.contains(comm_pt):
            inside = True
    except Exception:
        inside = False
    assigned_to_ratcha = (h_idx in h_in_set) if h_idx is not None and pd.notnull(h_idx) else False
    if inside or assigned_to_ratcha:
        comm_to_show.append((c_idx, h_idx, d))
        comm_to_show_set.add(c_idx)

# ---------- Identify linked CSMBS hospitals outside ราชเทวี that are assigned-to by communities INSIDE ราชเทวี ----------
linked_outside_hospitals_idx = set()
for c_idx, h_idx, d in comm_to_show:
    if h_idx is None or pd.isna(h_idx):
        continue
    if h_idx in h_in_set:
        continue
    # include only when the community itself is inside ราชเทวี
    try:
        comm_pt = ShapelyPoint(float(communities.loc[c_idx][LON_COL]), float(communities.loc[c_idx][LAT_COL]))
        if target_shape.contains(comm_pt):
            linked_outside_hospitals_idx.add(h_idx)
    except Exception:
        continue

# ---------- Build district metrics globally (for tooltip) ----------
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
                nm = district_names[i]
                district_metrics.setdefault(nm, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                district_metrics[nm]['num_hospitals'] += 1
                district_metrics[nm]['sum_hospital_weights'] += int(hosp.get('weight', 0) or 0)
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
                nm = district_names[i]
                district_metrics.setdefault(nm, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                district_metrics[nm]['num_communities'] += 1
                break
        except Exception:
            continue

global_max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)
props = target_feat.get('properties', {}) or {}
district_label = props.get(district_name_field) or props.get('name') or TARGET_DISTRICT_THAI
dm = district_metrics.get(district_label, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
props['district_name'] = district_label
props['amp_th'] = district_label
props['name'] = district_label
props['num_hospitals'] = int(dm.get('num_hospitals', 0))
props['num_communities'] = int(dm.get('num_communities', 0))
props['sum_hospital_weights'] = int(dm.get('sum_hospital_weights', 0))
props['choropleth_norm'] = float(props['sum_hospital_weights']) / float(global_max_sum_weights) if global_max_sum_weights > 0 else 0.0
highlight_feature = {"type":"Feature","geometry": target_feat.get('geometry'), "properties": props}

# ---------- Build folium map centered on district ----------
centroid = target_shape.centroid
center_point = [centroid.y, centroid.x]
m = folium.Map(location=center_point, zoom_start=15, tiles=None)

# Base tiles
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed district (hidden from LayerControl)
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
    tooltip=GeoJsonTooltip(fields=['district_name','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_fg)

# stroke layer (hidden)
bounds_gj = folium.GeoJson(
    data=district_geo,
    style_function=lambda feat: {'fillColor':'transparent','color':'#2c3e50','weight':2.6,'opacity':0.95,'interactive': True}
).add_to(districts_fg)

# ---------- CSMBS hospitals inside ราชเทวี ----------
csmbs_in_layer = FeatureGroup(name="CSMBS Hospitals (in ราชเทวี)", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
CSMBS_ICON_URI = try_file_name(CSMBS_ICON_FN)

for h_idx, hosp in csmbs_hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    weight = int(hosp.get('weight',0) or 0)
    near_pop = int(hosp.get(near_pop_col,0) or 0)
    beds = int(hosp.get(beds_col,0) or 0)
    district_val = hosp.get('เขต') or hosp.get('district') or props.get('district_name') or ''
    tel_val = hosp.get('tel') or hosp.get('โทรศัพท์') or ''
    url_val = hosp.get('url') or hosp.get('website') or ''
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:420px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{try_inline_image(HOSP_ICON_URI)}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {esc(district_val)}</div>
        <div><strong>เบอร์:</strong> {esc(tel_val)}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{esc(url_val)}" target="_blank" rel="noopener noreferrer">{esc(url_val)}</a></div>
        <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {weight}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """
    try:
        # use Hospital.png icon for all hospital markers (prefer this over CSMBS-specific)
        folium.Marker(location=[latf, lonf],
                      icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      popup=folium.Popup(popup_html, max_width=480),
                      tooltip=title_esc).add_to(csmbs_in_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#4caf50', fill=True, fill_color='#4caf50',
                            popup=folium.Popup(popup_html, max_width=480), tooltip=title_esc).add_to(csmbs_in_layer)

# ---------- Linked CSMBS hospitals (outside ราชเทวี) - use Hospital.png icon if available ----------
linked_hosp_layer = FeatureGroup(name="Linked Hospitals (outside ราชเทวี)", show=True, control=False).add_to(m)
for h_idx in sorted(linked_outside_hospitals_idx):
    try:
        hosp = csmbs_hospitals.loc[h_idx]
    except Exception:
        continue
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    weight = int(hosp.get('weight',0) or 0)
    near_pop = int(hosp.get(near_pop_col,0) or 0)
    beds = int(hosp.get(beds_col,0) or 0)
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:420px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">{title_esc}</div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขตของโรงพยาบาล:</strong> {esc(hosp.get('เขต') or hosp.get('district') or '')}</div>
        <div><strong>จำนวนชุมชนที่ถูกจับ:</strong> {weight}</div>
        <div><strong>จำนวนประชากรที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """
    # prefer to use Hospital.png as marker icon for linked outside hospitals
    try:
        folium.Marker(location=[latf, lonf],
                      icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title_esc).add_to(linked_hosp_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#1976d2', fill=True, fill_color='#1976d2',
                            popup=folium.Popup(popup_html, max_width=420),
                            tooltip=title_esc).add_to(linked_hosp_layer)

# ---------- Communities (indigo) and connections (gray) ----------
comm_layer = FeatureGroup(name="Communities (CSMBS connections)", show=True, control=False).add_to(m)
conn_layer = FeatureGroup(name="Filter Connections - สิทธิข้าราชการ", show=True, control=False).add_to(m)
HOUSE_ICON_URI = try_file_name(HOUSE_ICON_FN)

for c_idx, nearest_idx, dist_m in comm_assigned_csmbs:
    # show only communities selected (inside or assigned to in-district CSMBS)
    if c_idx not in comm_to_show_set:
        continue
    comm = communities.loc[c_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        continue
    comm_name = comm.get(comm_name_col,"")
    comm_pop = int(comm.get(comm_pop_col,0) or 0)
    if nearest_idx is not None and pd.notnull(nearest_idx):
        try:
            hosp = csmbs_hospitals.loc[nearest_idx]
            hosp_name = hosp.get(hosp_name_col,"")
        except Exception:
            hosp_name = "N/A"
        dist_text = f"{dist_m:.0f} m" if dist_m is not None else "N/A"
    else:
        hosp_name = "N/A"
        dist_text = "N/A"

    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:320px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{HOUSE_ICON_URI}" style="width:16px;height:16px;" alt="house" />
        <div>{html.escape(str(comm_name))}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>โรงพยาบาลที่รับสิทธิข้าราชการใกล้ที่สุด:</strong> {html.escape(str(hosp_name))}</div>
        <div><strong>ระยะ:</strong> {dist_text}</div>
        <div><strong>ประชากร:</strong> {comm_pop}</div>
      </div>
    </div>
    """

    folium.CircleMarker(location=[clat, clon],
                        radius=5.0,
                        color=CSMBS_COMM_COLOR,
                        fill=True,
                        fill_color=CSMBS_COMM_COLOR,
                        fill_opacity=0.95,
                        popup=folium.Popup(popup_html, max_width=360),
                        tooltip=str(comm_name)).add_to(comm_layer)

    # draw connection to assigned CSMBS hospital (gray) if available
    if nearest_idx is not None and pd.notnull(nearest_idx):
        try:
            hosp = csmbs_hospitals.loc[nearest_idx]
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
            folium.PolyLine(locations=[[clat, clon], [hlat, hlon]],
                            color=CSMBS_LINE_COLOR, weight=1.6, opacity=0.85).add_to(conn_layer)
        except Exception:
            pass

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; line-height:1.2 !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: bring district to back and bind click+tooltip ----------
district_var = district_gj.get_name()
bounds_var = bounds_gj.get_name()
map_var = m.get_name()
js_reorder_and_bind = f"""
<script>
(function(){{
  try {{
    var map = {map_var};
    var gj = {district_var};
    var bounds = {bounds_var};
    function reorder(){{
      try {{
        if (bounds && bounds.bringToBack) bounds.bringToBack();
        if (gj && gj.bringToBack) gj.bringToBack();
      }} catch(e) {{ console.warn('reorder err', e); }}
    }}
    setTimeout(reorder, 50);
    setTimeout(reorder, 300);
    setTimeout(reorder, 1000);

    if (gj && gj.eachLayer) {{
      gj.eachLayer(function(layer){{
        try {{
          layer.on('click', function(e) {{
            try {{
              if (window._lastDistrict && window._lastDistrict !== this) {{
                try {{ window._lastDistrict.setStyle({{color: window._lastDistrict.origColor || '#2c3e50', weight: window._lastDistrict.origWeight || 3, fillOpacity: window._lastDistrict.origFillOpacity || 0.22}}); }} catch(e){{}}
              }}
              if (!this.origColor) {{ this.origColor = this.options.color; this.origWeight = this.options.weight; this.origFillOpacity = this.options.fillOpacity; }}
              this.setStyle({{color:'#000000', weight:4, fillOpacity:0.35}});
              window._lastDistrict = this;
              if (this.getBounds) map.fitBounds(this.getBounds(), {{padding:[20,20]}});
            }} catch(err){{ console.warn(err); }}
          }});
          layer.on('mouseover', function(e){{ try{{ this.openTooltip(e.latlng); }}catch(e){{}} }});
          layer.on('mouseout', function(e){{ try{{ this.closeTooltip(); }}catch(e){{}} }});
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
print("CSMBS hospitals total:", len(csmbs_hospitals), "CSMBS hospitals in ราชเทวี:", len(csmbs_hospitals_in))
print("Linked outside CSMBS hospitals shown:", len(linked_outside_hospitals_idx))
print("Communities with CSMBS assignments shown:", len(comm_to_show))