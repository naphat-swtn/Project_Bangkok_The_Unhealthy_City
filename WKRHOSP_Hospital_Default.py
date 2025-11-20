# -*- coding: utf-8 -*-
"""
WKRHOSP_Hospital_Default.py

Generate WKRHOSP_Hospital_Default.html — focused view that shows only the hospital
named "โรงพยาบาลเวชการุณย์รัศมิ์" and the communities that are assigned (nearest)
to that hospital.

Behavior:
- Loads hospitals.csv, communities.csv, districts_bangkok.geojson.
- Computes global nearest-hospital assignment (communities -> nearest hospital).
- Finds hospital(s) with name matching "โรงพยาบาลเวชการุณย์รัศมิ์" (tries common name columns).
- Shows:
  - The selected hospital marker (uses Hospital.png if present).
  - Community markers for communities whose nearest hospital is that hospital.
  - Connection lines from those communities to the hospital.
  - Embedded district polygon for the district containing the hospital (tooltip + click-to-highlight).
- Outputs: WKRHOSP_Hospital_Default.html

Usage:
    python WKRHOSP_Hospital_Default.py
"""
import json
from pathlib import Path
import html
import sys
from collections import Counter

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
DISTRICTS_SRC = "districts_bangkok.geojson"
OUT_HTML = "WKRHOSP_Hospital_Default.html"
HOSP_ICON_FN = "Hospital.png"
HOUSE_ICON_FN = "House.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_HOSPITAL_NAME = "โรงพยาบาลเวชการุณย์รัศมิ์"

ICON_SIZE = (22, 22)
ICON_ANCHOR = (11, 11)

# ---------- Helpers ----------
def try_file_name(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

def try_inline_image(path):
    p = Path(path)
    if p.exists():
        import base64
        b = p.read_bytes()
        ext = p.suffix.lower()
        mime = "image/png"
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".svg":
            mime = "image/svg+xml"
        return "data:{};base64,{}".format(mime, base64.b64encode(b).decode("ascii"))
    return path

def detect_name_col(df_cols, candidates):
    for c in candidates:
        if c in df_cols:
            return c
    lc = {col.lower(): col for col in df_cols}
    for c in candidates:
        if c.lower() in lc:
            return lc[c.lower()]
    return None

def esc(s):
    return html.escape(str(s)) if s is not None else ''

# ---------- Load inputs ----------
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
hosp_name_col = detect_name_col(hospitals.columns, possible_hosp_name) or hospitals.columns[0]

possible_comm_name = ['ชุมชน','ชื่อชุมชน','community','name','ชื่อ']
comm_name_col = detect_name_col(communities.columns, possible_comm_name) or communities.columns[0]

# ensure numeric columns exist (kept for popups)
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Compute global nearest hospital assignment (communities -> nearest hospital) ----------
comm_assigned = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[LAT_COL]); comm_lon = float(comm[LON_COL])
    except Exception:
        comm_assigned.append((c_idx, None, None)); continue
    min_dist = float('inf'); nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            h_lat = float(hosp[LAT_COL]); h_lon = float(hosp[LON_COL])
        except Exception:
            continue
        d = geodesic((comm_lat, comm_lon), (h_lat, h_lon)).meters
        if d < min_dist:
            min_dist = d; nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_dist if min_dist != float('inf') else None))

# ---------- Compute robust hospital weights (number of communities assigned) ----------
assigned_idxs = [h_idx for (_c_idx, h_idx, _d) in comm_assigned if h_idx is not None and not pd.isna(h_idx)]
cnt = Counter(assigned_idxs)
# Map counts to hospital DataFrame index (works even when index is non-sequential)
hospitals['weight'] = hospitals.index.to_series().map(lambda idx: int(cnt.get(idx, 0))).astype(int)

# ---------- Find target hospital(s) by name ----------
matches = []
target_indices = []
for h_idx, hosp in hospitals.iterrows():
    name = str(hosp.get(hosp_name_col) or "").strip()
    if name == TARGET_HOSPITAL_NAME:
        matches.append((h_idx, hosp))
        target_indices.append(h_idx)
# try case-insensitive match if no exact match
if not matches:
    for h_idx, hosp in hospitals.iterrows():
        name = str(hosp.get(hosp_name_col) or "").strip()
        if name and name.lower() == TARGET_HOSPITAL_NAME.lower():
            matches.append((h_idx, hosp))
            target_indices.append(h_idx)

if not matches:
    raise SystemExit(f"Could not find hospital with name '{TARGET_HOSPITAL_NAME}' in {HOSPITALS_CSV}")

target_idx_set = set([h_idx for h_idx, _ in matches])

# ---------- Communities linked to the target hospital(s) ----------
linked_comms = []
for c_idx, h_idx, dist_m in comm_assigned:
    if h_idx in target_idx_set:
        linked_comms.append((c_idx, h_idx, dist_m))

# ---------- Find district feature that contains the target hospital (for highlight) ----------
district_features = districts_gj.get('features', []) or []
district_name_field = None
if district_features:
    props0 = district_features[0].get('properties', {}) or {}
    for candidate in ('amp_th','district','name','NAME','AMP_T','AMP_THA','DISTRICT'):
        if candidate in props0:
            district_name_field = candidate
            break
if district_name_field is None:
    district_name_field = 'amp_th'

target_district_feat = None
for h_idx, hosp in matches:
    try:
        pt = ShapelyPoint(float(hosp[LON_COL]), float(hosp[LAT_COL]))
    except Exception:
        continue
    for feat in district_features:
        geom = feat.get('geometry')
        if geom is None:
            continue
        try:
            poly = shape(geom)
            if poly.contains(pt):
                target_district_feat = feat
                break
        except Exception:
            continue
    if target_district_feat:
        break

# prepare highlight feature collection (if found)
if target_district_feat:
    props = target_district_feat.get('properties', {}) or {}
    label = props.get(district_name_field) or props.get('name') or "—"
    props['district_name'] = label
    props['amp_th'] = label
    props['name'] = label
    highlight_feature = {"type":"Feature", "geometry": target_district_feat.get('geometry'), "properties": props}
    district_geo = {"type":"FeatureCollection","features":[highlight_feature]}
else:
    district_geo = {"type":"FeatureCollection","features":[]}

# ---------- Build map centered on first matched hospital ----------
first_h_idx, first_hosp = matches[0]
center = [float(first_hosp[LAT_COL]), float(first_hosp[LON_COL])]
m = folium.Map(location=center, zoom_start=15, tiles=None)

# base tiles (Thai)
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed target district (hidden from LayerControl) if available
districts_fg = FeatureGroup(name="District (highlight)", show=True, control=False).add_to(m)
if district_geo['features']:
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
        tooltip=GeoJsonTooltip(fields=['district_name'],
                               aliases=['เขต:'],
                               localize=True, sticky=True)
    ).add_to(districts_fg)
    bounds_gj = folium.GeoJson(
        data=district_geo,
        style_function=lambda feat: {'fillColor':'transparent','color':'#2c3e50','weight':2.6,'opacity':0.95,'interactive': True}
    ).add_to(districts_fg)
else:
    # create empty placeholders so JS references don't break
    district_gj = folium.GeoJson(data={"type":"FeatureCollection","features":[]}).add_to(districts_fg)
    bounds_gj = folium.GeoJson(data={"type":"FeatureCollection","features":[]}).add_to(districts_fg)

# ---------- Hospital marker layer (only the target hospital(s)) ----------
hosp_layer = FeatureGroup(name=f"Hospitals - {TARGET_HOSPITAL_NAME}", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
for h_idx, hosp in matches:
    try:
        latf = float(hosp[LAT_COL]); lonf = float(hosp[LON_COL])
    except Exception:
        continue
    title = hosp.get(hosp_name_col) or ''
    title_esc = esc(title)
    district_val = hosp.get('เขต') or hosp.get('district') or ''
    weight = int(hosp.get('weight', 0) or 0)
    near_pop = int(hosp.get(near_pop_col, 0) or 0)
    beds = int(hosp.get(beds_col, 0) or 0)
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:380px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{try_inline_image(HOSP_ICON_URI)}" style="width:18px;height:18px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {esc(district_val)}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {weight}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """
    try:
        folium.Marker(location=[latf, lonf],
                      icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title_esc).add_to(hosp_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=7, color='#d32f2f', fill=True, fill_color='#d32f2f',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(hosp_layer)

# ---------- Communities linked to the target hospital(s) ----------
comm_layer = FeatureGroup(name="Communities (linked to target hospital)", show=True, control=False).add_to(m)
conn_layer = FeatureGroup(name="Connections (community → hospital)", show=True, control=False).add_to(m)
HOUSE_ICON_URI = try_file_name(HOUSE_ICON_FN)

for c_idx, h_idx, dist_m in linked_comms:
    comm = communities.loc[c_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        continue
    comm_name = comm.get(comm_name_col, "")
    comm_pop = int(comm.get(next((c for c in ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร'] if c in communities.columns), 'population'), 0) or 0)
    dist_text = f"{dist_m:.0f} m" if dist_m is not None else "N/A"
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:320px;">
      <div style="font-weight:700;font-size:15px;">{esc(comm_name)}</div>
      <div style="margin-top:8px;font-size:13px;line-height:1.35;">
        <div><strong>โรงพยาบาลใกล้ที่สุด:</strong> {esc(TARGET_HOSPITAL_NAME)}</div>
        <div><strong>ระยะ:</strong> {dist_text}</div>
        <div><strong>ประชากร:</strong> {comm_pop}</div>
      </div>
    </div>
    """
    folium.CircleMarker(location=[clat, clon], radius=4.5, color='#1976d2', fill=True, fill_color='#1976d2',
                        fill_opacity=0.95, popup=folium.Popup(popup_html, max_width=360),
                        tooltip=esc(comm_name)).add_to(comm_layer)

    # draw connection to the (first) hospital match location
    try:
        hlat = float(matches[0][1][LAT_COL]); hlon = float(matches[0][1][LON_COL])
        folium.PolyLine(locations=[[clat, clon], [hlat, hlon]], color='#2196F3', weight=1.2, opacity=0.6).add_to(conn_layer)
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

# ---------- JS: ensure district GeoJSON is behind markers and bind tooltip/click ----------
district_var = district_gj.get_name()
bounds_var = bounds_gj.get_name()
map_var = m.get_name()

js_template = """
<script>
(function(){
  try {
    var map = MAP_VAR;
    var gj = DIST_GJ_VAR;
    var bounds = BOUNDS_GJ_VAR;
    function reorder(){
      try {
        if (bounds && bounds.bringToBack) bounds.bringToBack();
        if (gj && gj.bringToBack) gj.bringToBack();
      } catch(e) { console.warn('reorder err', e); }
    }
    setTimeout(reorder, 50);
    setTimeout(reorder, 300);
    setTimeout(reorder, 1000);

    if (gj && gj.eachLayer) {
      gj.eachLayer(function(layer){
        try {
          if (layer.feature && layer.feature.properties && !layer.getTooltip) {
            var p = layer.feature.properties;
            var name = p['district_name'] || p['amp_th'] || p['name'] || '—';
            var numh = p['num_hospitals'] || 0;
            var numc = p['num_communities'] || 0;
            var html = "<div style='font-family: \"Bai Jamjuree\", sans-serif; font-size:15px; padding:6px; background:#EAF3FF; border:2px solid #6C7A89; border-radius:8px; color:#1A1A1A;'><div style='font-weight:700;'>" + name + "</div><div>จำนวนโรงพยาบาล: " + numh + "</div><div>จำนวนชุมชน: " + numc + "</div></div>";
            try { layer.bindTooltip(html, {sticky:true, direction:'auto', permanent:false}); } catch(e){}
          }

          layer.on('click', function(e){
            try {
              if (window._lastDistrict && window._lastDistrict !== this) {
                try { window._lastDistrict.setStyle({color: window._lastDistrict.origColor || '#2c3e50', weight: window._lastDistrict.origWeight || 3, fillOpacity: window._lastDistrict.origFillOpacity || 0.22}); } catch(e){}
              }
              if (!this.origColor) { this.origColor = this.options.color; this.origWeight = this.options.weight; this.origFillOpacity = this.options.fillOpacity; }
              this.setStyle({color:'#000000', weight:4, fillOpacity:0.35});
              window._lastDistrict = this;
              if (this.getBounds) map.fitBounds(this.getBounds(), {padding:[20,20]});
            } catch(err){ console.warn(err); }
          });
          layer.on('mouseover', function(e){ try{ this.openTooltip(e.latlng); }catch(e){} });
          layer.on('mouseout', function(e){ try{ this.closeTooltip(); }catch(e){} });
        } catch(e){ console.warn('bind err', e); }
      });
    }
  } catch(e){ console.warn('init err', e); }
})();
</script>
"""
js = js_template.replace("MAP_VAR", map_var).replace("DIST_GJ_VAR", district_var).replace("BOUNDS_GJ_VAR", bounds_var)
m.get_root().html.add_child(folium.Element(js))

# ---------- LayerControl and save ----------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)

print("Saved:", OUT_HTML)
print(f"Hospital shown: {len(matches)} match(es) for '{TARGET_HOSPITAL_NAME}'")
print(f"Communities linked to this hospital shown: {len(linked_comms)}")