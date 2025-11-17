# -*- coding: utf-8 -*-
"""
BKK_Hospital_Distance_CSMBS.py

Generates:
- BKK_Hospital_Distance_CSMBS.html

Purpose:
- Shows hospitals that accept "สิทธิข้าราชการ" (CSMBS / civil servant medical benefit)
  and nearby communities linked to their nearest CSMBS hospital.
- Shows "Filter Connections - สิทธิข้าราชการ" connections (gray lines).
- Community markers are purple (stroke + fill).
- Community popup text: "โรงพยาบาลที่รับสิทธิข้าราชการใกล้ที่สุด:" with distance and population.
- Hospital popups show detailed info (name, เขต, จำนวนชุมชนใกล้เคียง, จำนวนประชากรใกล้เคียงที่ต้องรองรับ, จำนวนเตียง).
- Districts embedded (fill + bounds) and moved to back so markers are clickable.
- Column name to detect in hospitals.csv: "สิทธิข้าราชการ" (also attempts some reasonable alternates).

Usage:
  Place in folder with:
    - hospitals.csv
    - communities.csv
    - districts.geojson
    - Hospital.png
    - House.png
  Run:
    python BKK_Hospital_Distance_CSMBS.py
  Serve:
    python -m http.server 8000
  Open:
    http://localhost:8000/BKK_Hospital_Distance_CSMBS.html
"""
import json
from pathlib import Path
import html
import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape
from shapely.geometry import Point as ShapelyPoint
from geopy.distance import geodesic
import sys

# ---------- Config ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
DISTRICTS_SRC = "districts.geojson"
OUT_HTML = "BKK_Hospital_Distance_CSMBS.html"

HOSP_ICON_FN = "Hospital.png"
HOUSE_ICON_FN = "House.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'

# Colors
CSMBS_LINE_COLOR = "#9E9E9E"    # gray for connection lines
CSMBS_MARKER_COLOR = "#9C27B0"  # purple for communities (stroke + fill)

# ---------- Helpers ----------
def try_file_name(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

def detect_csmbs_column(df_cols):
    # include the exact user-provided header plus common variants
    candidates = [
        'สิทธิข้าราชการ',     # user's exact column name
        'CSMBS',
        'ข้าราชการ',
        'civil_servant',
        'civil_service',
        'รับสิทธิข้าราชการ',
        'รับ_csmbs',
        'รับ_csm'
    ]
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
        if float(s) > 0:
            return True
    except Exception:
        pass
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

# ---------- Load CSVs ----------
if not Path(HOSPITALS_CSV).exists() or not Path(COMMUNITIES_CSV).exists():
    print("Missing hospitals.csv or communities.csv in working directory.", file=sys.stderr)
    sys.exit(1)

hospitals = pd.read_csv(HOSPITALS_CSV)
communities = pd.read_csv(COMMUNITIES_CSV)
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Hospital coords columns '{LAT_COL}'/'{LON_COL}' not found in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Community coords columns '{LAT_COL}'/'{LON_COL}' not found in {COMMUNITIES_CSV}")

# detect name columns
possible_hosp_name = ['โรงพยาบาล','โรงพาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name if c in hospitals.columns), hospitals.columns[0])
possible_comm_name = ['ชุมชน','ชื่อชุมชน','community','name','ชื่อ']
comm_name_col = next((c for c in possible_comm_name if c in communities.columns), communities.columns[0])

# community population col (optional)
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

# ---------- Identify CSMBS hospitals ----------
csmbs_col = detect_csmbs_column(hospitals.columns)
if csmbs_col:
    hospitals['csmbs_accept'] = hospitals[csmbs_col].apply(truthy)
else:
    # fallback: attempt to find keywords in notes/type columns
    hospitals['csmbs_accept'] = False
    for col in hospitals.columns:
        if col.lower() in ('note','notes','type','remark','comment'):
            hospitals['csmbs_accept'] = hospitals['csmbs_accept'] | hospitals[col].astype(str).str.contains('ข้าราชการ|CSMBS|civil', case=False, na=False)

csmbs_hospitals = hospitals[hospitals['csmbs_accept'] == True].copy()
print(f"Detected CSMBS column: {csmbs_col}; CSMBS hospitals found: {len(csmbs_hospitals)}")

# ---------- Read districts.geojson and prepare features ----------
dist_path = Path(DISTRICTS_SRC)
if not dist_path.exists():
    print(f"{DISTRICTS_SRC} not found.", file=sys.stderr)
    sys.exit(1)

with dist_path.open('r', encoding='utf-8') as f:
    districts_gj = json.load(f)

district_features = districts_gj.get('features', []) or []
district_name_field = detect_name_field(district_features)

# build shapely polygons and names
district_shapes = []
district_names = []
for feat in district_features:
    geom = feat.get('geometry')
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field) if district_name_field else None
    district_names.append(name)
    if geom is None:
        district_shapes.append(None)
    else:
        try:
            district_shapes.append(shape(geom))
        except Exception:
            district_shapes.append(None)

# ---------- Compute nearest CSMBS hospital for each community ----------
comm_assigned_csmbs = []
for c_idx, comm in communities.iterrows():
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        comm_assigned_csmbs.append((c_idx, None, None)); continue
    min_d = float('inf'); nearest_idx = None
    for h_idx, hosp in csmbs_hospitals.iterrows():
        try:
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((clat, clon), (hlat, hlon)).meters
        if d < min_d:
            min_d = d; nearest_idx = h_idx
    comm_assigned_csmbs.append((c_idx, nearest_idx, min_d if min_d != float('inf') else None))

# compute CSMBS hospital weights (# communities assigned)
csmbs_hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned_csmbs:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            csmbs_hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Compute district metrics (use all hospitals for consistency) ----------
district_metrics = {name: {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0} for name in district_names}

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
                m = district_metrics.setdefault(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                m['num_hospitals'] += 1
                m['sum_hospital_weights'] += int(hosp.get('weight', 0) or 0)
                break
        except Exception:
            continue

# assign communities to districts
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

# ---------- Build modified district features (inject properties) ----------
out_features = []
for i, feat in enumerate(district_features):
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field) if district_name_field else None
    metrics = district_metrics.get(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
    props['district_name'] = name or (props.get('name') or props.get('NAME') or '—')
    props['amp_th'] = props['district_name']
    props['name'] = props['district_name']
    props['num_hospitals'] = int(metrics.get('num_hospitals',0))
    props['num_communities'] = int(metrics.get('num_communities',0))
    props['sum_hospital_weights'] = int(metrics.get('sum_hospital_weights',0))
    out_features.append({"type":"Feature","geometry":feat.get('geometry'), "properties":props})

# add normalized value
global_max = max((f['properties'].get('sum_hospital_weights',0) for f in out_features), default=1)
for f in out_features:
    s = f['properties'].get('sum_hospital_weights', 0)
    f['properties']['choropleth_norm'] = float(s) / float(global_max) if global_max > 0 else 0.0

# ---------- Build folium map ----------
center = [float(communities[LAT_COL].astype(float).mean()), float(communities[LON_COL].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# Base tiles (Thai)
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# Districts: embed (hidden from LayerControl)
districts_fg = FeatureGroup(name="Districts (fill + bounds)", show=True, control=False).add_to(m)
district_gj = folium.GeoJson(
    data={"type":"FeatureCollection","features":out_features},
    style_function=lambda feat: {
        'fillColor': '#3388ff',
        'color': '#2c3e50',
        'weight': 3,
        'fillOpacity': 0.18,
        'opacity': 0.95,
        'interactive': True
    },
    highlight_function=lambda feat: {
        'weight': 4,
        'color': '#000000',
        'fillOpacity': 0.45
    },
    tooltip=GeoJsonTooltip(fields=['amp_th','num_hospitals','num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, sticky=True)
).add_to(districts_fg)

# stroke-only layer (also hidden)
bounds_gj = folium.GeoJson(
    data={"type":"FeatureCollection","features":out_features},
    style_function=lambda feat: {'fillColor':'transparent','color':'#2c3e50','weight':2.6,'opacity':0.95,'interactive': True}
).add_to(districts_fg)

# ---------- CSMBS Hospitals layer ----------
csmbs_layer = FeatureGroup(name="CSMBS Hospitals (สิทธิข้าราชการ)", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
for _, row in csmbs_hospitals.iterrows():
    try:
        latf = float(row[LAT_COL]); lonf = float(row[LON_COL])
    except Exception:
        continue
    title = row.get(hosp_name_col) or ''
    title_esc = html.escape(str(title))
    weight = int(row.get('weight', 0) or 0)
    near_pop = int(row.get(near_pop_col, 0) or 0)
    beds = int(row.get(beds_col, 0) or 0)
    district_val = row.get('เขต') or row.get('district') or ''
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:380px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:18px;height:18px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {html.escape(str(district_val))}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {weight}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """
    try:
        folium.Marker(location=[latf, lonf],
                      icon=folium.CustomIcon(HOSP_ICON_URI, (22,22), (11,11)),
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title_esc).add_to(csmbs_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#c62828', fill=True, fill_color='#c62828',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(csmbs_layer)

# ---------- Communities (purple) and CSMBS Connections (gray lines) ----------
HOUSE_ICON_URI = try_file_name(HOUSE_ICON_FN)
comm_layer = FeatureGroup(name="Communities (CSMBS connections)", show=True, control=False).add_to(m)
conn_layer = FeatureGroup(name="Filter Connections - สิทธิข้าราชการ", show=True, control=False).add_to(m)

for comm_idx, nearest_idx, dist_m in comm_assigned_csmbs:
    comm = communities.loc[comm_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        continue
    comm_name = comm.get(comm_name_col,"")
    comm_pop = int(comm.get(comm_pop_col,0) or 0)
    if nearest_idx is not None and pd.notnull(nearest_idx):
        hosp = csmbs_hospitals.loc[nearest_idx]
        hosp_name = hosp.get(hosp_name_col,"")
        dist_text = f"{dist_m:.0f} m" if dist_m is not None else "N/A"
        try:
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            hlat = hlon = None
    else:
        hosp_name = "N/A"
        dist_text = "N/A"
        hlat = hlon = None

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

    # purple circle marker (stroke + fill)
    folium.CircleMarker(location=[clat, clon],
                        radius=5.0,
                        color=CSMBS_MARKER_COLOR,
                        fill=True,
                        fill_color=CSMBS_MARKER_COLOR,
                        fill_opacity=0.95,
                        popup=folium.Popup(popup_html, max_width=360),
                        tooltip=str(comm_name)).add_to(comm_layer)

    # connection polyline (gray)
    if hlat is not None and hlon is not None:
        folium.PolyLine(locations=[[clat, clon], [hlat, hlon]],
                        color=CSMBS_LINE_COLOR, weight=1.6, opacity=0.85).add_to(conn_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; line-height:1.2 !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: bring districts to back and bind click+tooltip events on district features ----------
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

    // bind persistent click highlight (if gj exists)
    if (gj && gj.eachLayer) {{
      gj.eachLayer(function(layer){{
        try {{
          layer.on('click', function(e) {{
            try {{
              if (window._lastDistrict && window._lastDistrict !== this) {{
                try {{ window._lastDistrict.setStyle({{color: window._lastDistrict.origColor || '#2c3e50', weight: window._lastDistrict.origWeight || 3, fillOpacity: window._lastDistrict.origFillOpacity || 0.18}}); }} catch(e){{}}
              }}
              if (!this.origColor) {{ this.origColor = this.options.color; this.origWeight = this.options.weight; this.origFillOpacity = this.options.fillOpacity; }}
              this.setStyle({{color:'#000000', weight:4, fillOpacity:0.35}});
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

# ---------- LayerControl (only base maps shown) and save ----------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print("Saved:", OUT_HTML)
print("CSMBS hospitals:", len(csmbs_hospitals), "CSMBS connections drawn:", sum(1 for c in comm_assigned_csmbs if c[1] is not None))