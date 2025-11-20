# -*- coding: utf-8 -*-
"""
Ratchathewi_Hospital_Distance_Default.py

Same as previous version but now also shows hospitals that are located outside
เขตราชเทวี but are the assigned (nearest) hospital for at least one
community that is inside ราชเทวี. These are shown in a separate layer
"Linked Hospitals (outside ราชเทวี)" so you can see cross-district links.

Usage:
  python Ratchathewi_Hospital_Distance_Default.py
"""
import json
from pathlib import Path
import html
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
OUT_HTML = "Ratchathewi_Hospital_Distance_Default.html"
HOSP_ICON_FN = "Hospital.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_DISTRICT_THAI = "ราชเทวี"

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

# ---------- Sanity checks ----------
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

# detect population column for communities
possible_pop_cols = ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร']
comm_pop_col = next((c for c in possible_pop_cols if c in communities.columns), None)
if comm_pop_col is None:
    communities['population'] = 0
    comm_pop_col = 'population'
else:
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col, 0), errors='coerce').fillna(0).astype(int)

# ensure hospital numeric fields
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Build district shapes and detect name field ----------
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

# ---------- Global assignment: nearest hospital for each community ----------
comm_assigned_global = []  # (comm_idx, nearest_h_idx or None, distance_meters)
for c_idx, comm in communities.iterrows():
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        comm_assigned_global.append((c_idx, None, None)); continue
    min_d = float('inf'); nearest = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((clat, clon), (hlat, hlon)).meters
        if d < min_d:
            min_d = d; nearest = h_idx
    comm_assigned_global.append((c_idx, nearest, min_d if min_d != float('inf') else None))

# compute per-hospital global metrics
h_metrics_global = {h_idx: {'num_communities': 0, 'sum_population': 0} for h_idx in hospitals.index}
for c_idx, h_idx, d in comm_assigned_global:
    if h_idx is not None and pd.notnull(h_idx):
        pop = int(communities.loc[c_idx].get(comm_pop_col, 0) or 0)
        h_metrics_global.setdefault(h_idx, {'num_communities': 0, 'sum_population': 0})
        h_metrics_global[h_idx]['num_communities'] += 1
        h_metrics_global[h_idx]['sum_population'] += pop

global_max_comm = max((v['num_communities'] for v in h_metrics_global.values()), default=1)

# ---------- Find target district feature and polygon ----------
target_feat = None
for feat in district_features:
    props = feat.get('properties') or {}
    name_val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
    if name_val == TARGET_DISTRICT_THAI:
        target_feat = feat
        break
if target_feat is None:
    for feat in district_features:
        props = feat.get('properties') or {}
        name_val = str(props.get(district_name_field) or props.get('name') or props.get('district_name') or '').strip()
        if name_val and name_val.lower() == TARGET_DISTRICT_THAI.lower():
            target_feat = feat
            break
if target_feat is None:
    raise SystemExit(f"Could not find district '{TARGET_DISTRICT_THAI}' in {DISTRICTS_SRC}")

target_shape = shape(target_feat.get('geometry'))

# ---------- Hospitals inside Ratchathewi (displayed) ----------
hospitals_in = []
h_in_set = set()
for h_idx, hosp in hospitals.iterrows():
    try:
        pt = ShapelyPoint(float(hosp[LON_COL]), float(hosp[LAT_COL]))
    except Exception:
        continue
    try:
        if target_shape.contains(pt):
            hospitals_in.append((h_idx, hosp))
            h_in_set.add(h_idx)
    except Exception:
        continue

# ---------- Communities to show:
# - communities that are inside Ratchathewi OR
# - communities (outside) whose assigned nearest hospital is in Ratchathewi
comm_to_show = []
for c_idx, h_idx, d in comm_assigned_global:
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

# ---------- Identify hospitals outside Ratchathewi that are assigned-to by at least one community inside Ratchathewi ----------
linked_outside_hospitals_idx = set()
for c_idx, h_idx, d in comm_to_show:
    # We only want hospitals that are assigned from communities_in Ratchathewi but located outside
    if h_idx is None or pd.isna(h_idx):
        continue
    if h_idx in h_in_set:
        continue  # skip those already inside
    # check whether the community itself is inside Ratchathewi
    try:
        comm_pt = ShapelyPoint(float(communities.loc[c_idx][LON_COL]), float(communities.loc[c_idx][LAT_COL]))
        if target_shape.contains(comm_pt):
            linked_outside_hospitals_idx.add(h_idx)
    except Exception:
        continue

# ---------- Prepare embedded district feature (inject global metrics for this district) ----------
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
                district_metrics[nm]['sum_hospital_weights'] += int(h_metrics_global.get(h_idx, {}).get('num_communities', 0) or 0)
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

# base tiles
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

# ---------- Hospitals in district ----------
hosp_layer = FeatureGroup(name="Hospitals in ราชเทวี", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
ICON_SIZE = (18,18); ICON_ANCHOR = (9,9)

for h_idx, hosp in hospitals_in:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    num_c = h_metrics_global.get(h_idx, {}).get('num_communities', 0)
    sum_pop = h_metrics_global.get(h_idx, {}).get('sum_population', 0)
    normalized = (math.sqrt(num_c) / math.sqrt(global_max_comm)) if global_max_comm > 0 else 0.0
    # color/size
    def mix_colors(hex1, hex2, t):
        def hex_to_rgb(hx): hx=hx.lstrip('#'); return tuple(int(hx[i:i+2],16) for i in (0,2,4))
        def rgb_to_hex(rgb): return '#{:02x}{:02x}{:02x}'.format(*[int(max(0,min(255,round(v)))) for v in rgb])
        r1,g1,b1 = hex_to_rgb(hex1); r2,g2,b2 = hex_to_rgb(hex2)
        r = r1 + (r2-r1)*t; g = g1 + (g2-g1)*t; b = b1 + (b2-b1)*t
        return rgb_to_hex((r,g,b))
    color_hex = mix_colors('#ffdede', '#b71c1c', normalized)
    radius = 6 + normalized * 30
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:360px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">{esc(title)}</div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {esc(props['district_name'])}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {num_c}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {sum_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {int(hosp.get(beds_col,0) or 0)}</div>
      </div>
    </div>
    """
    folium.CircleMarker(location=[latf, lonf], radius=radius, color=color_hex, fill=True, fill_color=color_hex,
                        fill_opacity=0.9, weight=1.2, popup=folium.Popup(popup_html, max_width=420),
                        tooltip=title_esc).add_to(hosp_layer)
    try:
        folium.Marker(location=[latf, lonf], icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      tooltip=f"{title} (center)").add_to(hosp_layer)
    except Exception:
        pass

# ---------- Linked hospitals (outside ราชเทวี) that are assigned-to by communities INSIDE ราชเทวี ----------
linked_hosp_layer = FeatureGroup(name="Linked Hospitals (outside ราชเทวี)", show=True, control=False).add_to(m)
for h_idx in sorted(linked_outside_hospitals_idx):
    try:
        hosp = hospitals.loc[h_idx]
    except Exception:
        continue
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    num_c = h_metrics_global.get(h_idx, {}).get('num_communities', 0)
    sum_pop = h_metrics_global.get(h_idx, {}).get('sum_population', 0)
    normalized = (math.sqrt(num_c) / math.sqrt(global_max_comm)) if global_max_comm > 0 else 0.0
    color_hex = '#1976d2'  # blue to indicate outside linked hospitals
    radius = 5 + normalized * 18
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:360px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">{esc(title)}</div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขตของโรงพยาบาล:</strong> {esc(hosp.get('เขต') or hosp.get('district') or '')}</div>
        <div><strong>จำนวนชุมชนที่ถูกจับ:</strong> {num_c}</div>
        <div><strong>จำนวนประชากรที่ต้องรองรับ:</strong> {sum_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {int(hosp.get(beds_col,0) or 0)}</div>
      </div>
    </div>
    """
    folium.CircleMarker(location=[latf, lonf], radius=radius, color=color_hex, fill=True, fill_color=color_hex,
                        fill_opacity=0.95, weight=1.0, popup=folium.Popup(popup_html, max_width=420),
                        tooltip=f"{title} — linked to communities in ราชเทวี").add_to(linked_hosp_layer)
    try:
        folium.Marker(location=[latf, lonf], icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      tooltip=f"{title} (center)").add_to(linked_hosp_layer)
    except Exception:
        pass

# ---------- Communities to show (include outside ones assigned to hospitals_in) ----------
comm_layer = FeatureGroup(name="Communities (inside or assigned to hospitals in ราชเทวี)", show=True, control=False).add_to(m)
for c_idx, assigned_h, dist_m in comm_to_show:
    comm = communities.loc[c_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        continue
    comm_name = comm.get(comm_name_col,"")
    comm_pop = int(comm.get(comm_pop_col,0) or 0)
    if assigned_h is not None and pd.notnull(assigned_h):
        hosp_name = hospitals.loc[assigned_h].get(hosp_name_col,"")
        dist_text = f"{dist_m:.0f} m" if dist_m is not None else "N/A"
    else:
        hosp_name = "N/A"; dist_text = "N/A"
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:320px;">
      <div style="font-weight:700;font-size:15px;">{esc(comm_name)}</div>
      <div style="margin-top:8px;font-size:13px;line-height:1.35;">
        <div><strong>โรงพยาบาลใกล้ที่สุด:</strong> {esc(hosp_name)}</div>
        <div><strong>ระยะ:</strong> {dist_text}</div>
        <div><strong>ประชากร:</strong> {comm_pop}</div>
      </div>
    </div>
    """
    folium.CircleMarker(location=[clat, clon], radius=4.5, color='#1976d2', fill=True, fill_color='#1976d2',
                        fill_opacity=0.95, popup=folium.Popup(popup_html, max_width=360),
                        tooltip=str(comm_name)).add_to(comm_layer)

# ---------- Connections from shown communities to their assigned hospital (if assigned hospital exists) ----------
conn_layer = FeatureGroup(name="Connections (community → hospital)", show=True, control=False).add_to(m)
for c_idx, assigned_h, dist_m in comm_to_show:
    if assigned_h is None or pd.isna(assigned_h):
        continue
    try:
        comm = communities.loc[c_idx]
        hosp = hospitals.loc[assigned_h]
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
        hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
    except Exception:
        continue
    folium.PolyLine(locations=[[clat, clon], [hlat, hlon]], color='#2196F3', weight=1.2, opacity=0.6).add_to(conn_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; line-height:1.2 !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: bind robust tooltip open/close and click-to-highlight for embedded district polygon ----------
district_var = district_gj.get_name()
map_var = m.get_name()
js = f"""
<script>
(function(){{
  try {{
    var map = {map_var};
    var gj = {district_var};
    function reorder(){{
      try {{
        if (gj && gj.bringToBack) gj.bringToBack();
      }} catch(e){{ console.warn('reorder err',e); }}
    }}
    setTimeout(reorder,50); setTimeout(reorder,300); setTimeout(reorder,1000);

    if (gj && gj.eachLayer) {{
      gj.eachLayer(function(layer){{
        try {{
          if (!layer.getTooltip || !layer.bindTooltip) {{
            return;
          }}
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
              if (this.getBounds) map.fitBounds(this.getBounds(), {{padding:[20,20]}});
            }} catch(err){{ console.warn(err); }}
          }});
        }} catch(e){{ console.warn('bind err', e); }}
      }});
    }}
  }} catch(e){{ console.warn('init err', e); }}
}})();
</script>
"""
m.get_root().html.add_child(folium.Element(js))

# ---------- LayerControl and save ----------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print("Saved:", OUT_HTML)
print(f"Hospitals in {TARGET_DISTRICT_THAI}: {len(hospitals_in)}")
print(f"Linked hospitals outside ราชเทวี shown: {len(linked_outside_hospitals_idx)}")
print(f"Communities shown (inside or assigned-to-district hospitals): {len(comm_to_show)}")