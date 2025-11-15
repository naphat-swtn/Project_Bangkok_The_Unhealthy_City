"""
BKK_Hospital_BedNumber.py

Produces BKK_Hospital_BedNumber.html

This view follows the BKK_Hospital_Default design system (Thai tile names, Bai Jamjuree font,
districts combined fill+stroke with stroke color #2c3e50, mobile touch support for district tooltips),
but shows only the "Hospitals (by beds) - marker size" filter as the main visible filter.

Behavior:
- Loads hospitals.csv, communities.csv, districts_bangkok.geojson.
- Computes nearest-assignment (community -> nearest hospital) to derive hospital weight (number of nearby communities).
- Builds a marker-size layer where marker radius and color intensity are derived from the hospital's
  "จำนวนเตียง" column (beds).
- Markers show popup with:
    - ชื่อโรงพยาบาล
    - เขต
    - จำนวนชุมชนใกล้เคียง (weight)
    - จำนวนประชากรใกล้เคียงที่ต้องรองรับ
    - จำนวนเตียง
- Districts combined (fill + bounds) layer is present with tooltip (fields: name, num_hospitals, num_communities).
- Mobile touch support injected to allow tap-to-toggle tooltips on districts.
- TileLayers named in Thai: 'แผนที่แบบหยาบ' and 'แผนที่แบบละเอียด'.
- LayerControl added (so base maps are selectable).
- Output HTML: BKK_Hospital_BedNumber.html
"""
import json
import base64
from pathlib import Path
import html
import math
import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip, GeoJsonPopup
from shapely.geometry import shape, Point
from geopy.distance import geodesic

# -------------------------
# Config / paths
# -------------------------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "BKK_Hospital_BedNumber.html"

HOSP_ICON_FN = "Hospital.png"
PUSH_PIN_FN = "RoundPushpin.png"

# -------------------------
# Helper: inline image -> data URI if available
# -------------------------
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

HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)
PUSH_PIN_URI = try_inline_image(PUSH_PIN_FN)

# center icon size/anchor (kept small)
ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

# -------------------------
# Load CSVs and geojson
# -------------------------
hospitals = pd.read_csv(HOSPITALS_CSV)
communities = pd.read_csv(COMMUNITIES_CSV)
with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# -------------------------
# Sanitize / detect columns
# -------------------------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

lat_col = 'ละติจูด'
lon_col = 'ลองจิจูด'
if lat_col not in hospitals.columns or lon_col not in hospitals.columns:
    raise KeyError(f"Expected hospital coords columns '{lat_col}', '{lon_col}' in {HOSPITALS_CSV}")
if lat_col not in communities.columns or lon_col not in communities.columns:
    raise KeyError(f"Expected community coords columns '{lat_col}', '{lon_col}' in {COMMUNITIES_CSV}")

possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# ensure numeric columns used in popup exist
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# -------------------------
# Compute nearest assignment (communities -> nearest hospital)
# -------------------------
comm_assigned = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[lat_col]); comm_lon = float(comm[lon_col])
    except Exception:
        comm_assigned.append((c_idx, None, None))
        continue
    min_dist = float('inf'); nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            h_lat = float(hosp[lat_col]); h_lon = float(hosp[lon_col])
        except Exception:
            continue
        d = geodesic((comm_lat, comm_lon), (h_lat, h_lon)).meters
        if d < min_dist:
            min_dist = d; nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_dist if min_dist != float('inf') else None))

# hospital weight = number of communities assigned
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# -------------------------
# Compute district metrics and write into geojson properties BEFORE creating GeoJson with tooltip
# -------------------------
district_features = bangkok_geo.get('features', [])
district_name_field = 'amp_th'  # adjust if geojson uses different property name

# prepare shapely polygons for point-in-polygon
district_shapes = []
for feat in district_features:
    geom = feat.get('geometry')
    district_shapes.append(shape(geom) if geom is not None else None)

# init metrics
district_metrics = {}
for feat in district_features:
    nm = feat.get('properties', {}).get(district_name_field)
    district_metrics[nm] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}

# assign hospitals to district and accumulate metrics
for h_idx, h in hospitals.iterrows():
    try:
        pt = Point(h[lon_col], h[lat_col])
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None:
            continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
                district_metrics[name]['num_hospitals'] += 1
                district_metrics[name]['sum_hospital_weights'] += int(h.get('weight', 0) or 0)
                break
        except Exception:
            continue

# assign communities to district
for c_idx, c in communities.iterrows():
    try:
        pt = Point(c[lon_col], c[lat_col])
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None:
            continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
                district_metrics[name]['num_communities'] += 1
                break
        except Exception:
            continue

max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

# write metrics into geojson feature properties
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / max_sum_weights) if max_sum_weights > 0 else 0.0

# -------------------------
# Create map (Design System base)
# -------------------------
center = [float(communities[lat_col].astype(float).mean()), float(communities[lon_col].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# Base tiles with Thai names
folium.TileLayer(
    tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
    name='แผนที่แบบหยาบ',
    control=True,
    show=True
).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', show=False, control=True).add_to(m)

# -------------------------
# Districts: combined fill + bounds (Design System)
# -------------------------
def combined_district_style(feature):
    return {
        'fillColor': '#3388ff',
        'color': '#2c3e50',
        'weight': 3,
        'fillOpacity': 0.2,
        'opacity': 0.95
    }

districts_layer = FeatureGroup(name="Districts (fill + bounds)", show=True, control=False).add_to(m)
gj = folium.GeoJson(
    data=bangkok_geo,
    style_function=combined_district_style,
    tooltip=GeoJsonTooltip(fields=[district_name_field, 'num_hospitals', 'num_communities'],
                           aliases=['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:'],
                           localize=True, labels=True, sticky=True),
    name="Districts Combined"
).add_to(districts_layer)

# -------------------------
# Marker-size layer (Hospitals by beds) - VISIBLE (only main filter)
# -------------------------
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

small_color_hex = '#ffdede'
large_color_hex = '#b71c1c'
min_radius = 6
max_radius = 36
max_beds = hospitals[beds_col].max() if len(hospitals) > 0 else 0

beds_layer = FeatureGroup(name="Hospitals (by beds) - marker size", show=True, control=False).add_to(m)

for _, row in hospitals.iterrows():
    try:
        latf = float(row[lat_col]); lonf = float(row[lon_col])
    except Exception:
        continue
    val = int(row.get(beds_col, 0))
    normalized = (math.sqrt(val) / math.sqrt(max_beds)) if max_beds > 0 else 0.0
    radius = min_radius + normalized * (max_radius - min_radius)
    color_hex = mix_colors(small_color_hex, large_color_hex, normalized)
    fill_opacity = 0.35 + 0.6 * normalized
    stroke_weight = 1 + 2 * normalized

    # Build popup with required fields (same simplified set)
    title = row.get('โรงพยาบาล') or row.get(hosp_name_col) or ''
    title_esc = html.escape(str(title))
    district_val = row.get('เขต') or row.get('district') or ''
    district_esc = html.escape(str(district_val))
    weight = int(row.get('weight', 0))
    near_pop = int(row.get(near_pop_col, 0))
    beds = int(row.get(beds_col, 0))

    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; max-width:360px;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:18px;height:18px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px; font-size:14px;">
        <div><strong>เขต:</strong> {district_esc}</div>
        <div><strong>จำนวนชุมชนใกล้เคียง:</strong> {weight}</div>
        <div><strong>จำนวนประชากรใกล้เคียงที่ต้องรองรับ:</strong> {near_pop}</div>
        <div><strong>จำนวนเตียง:</strong> {beds}</div>
      </div>
    </div>
    """

    # big sized circle (beds-based)
    folium.CircleMarker(
        location=[latf, lonf],
        radius=radius,
        color=color_hex,
        fill=True, fill_color=color_hex, fill_opacity=fill_opacity,
        weight=stroke_weight,
        popup=folium.Popup(popup_html, max_width=420),
        tooltip=f"{title} — {val} เตียง"
    ).add_to(beds_layer)

    # center icon
    try:
        icon = folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(
            location=[latf, lonf],
            icon=icon,
            tooltip=f"{title} (center)"
        ).add_to(beds_layer)
    except Exception:
        folium.CircleMarker(
            location=[latf, lonf],
            radius=7,
            color='#d32f2f', fill=True, fill_color='#d32f2f', fill_opacity=1.0,
            weight=0.6,
            tooltip=f"{title} (center)"
        ).add_to(beds_layer)

# -------------------------
# Simple CSS: Bai Jamjuree for tooltips and LayerControl 16px
# -------------------------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label {
  font-family: 'Bai Jamjuree', sans-serif !important;
  font-size: 16px !important;
  line-height: 1.2 !important;
}
.hospital-popup, .leaflet-tooltip.district-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# -------------------------
# Inject JS: mobile touch support for GeoJson tooltips (tap to open/close)
# -------------------------
gj_var = gj.get_name()
map_var = m.get_name()
js_touch_template = """
<script>
(function(){
  try {
    var isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints>0);
    if (!isTouch) return;

    var map = {MAP_VAR};
    var GJ_NAME = '{GJ_VAR}';
    var openLayer = null;

    function bindTouchBehaviorToGeoJson(gj) {
      try {
        if (!gj || !gj.eachLayer) return;
        gj.eachLayer(function(layer){
          try {
            if (layer._touchBound) return;
            layer.on('click touchstart', function(e){
              try {
                if (openLayer && openLayer !== this) {
                  try { openLayer.closeTooltip(); } catch(err){}
                  openLayer = null;
                }
                var already = (openLayer === this);
                if (already) {
                  try { this.closeTooltip(); } catch(err){}
                  openLayer = null;
                } else {
                  try { this.openTooltip(e.latlng); } catch(err){}
                  openLayer = this;
                }
              } catch(err){ console.warn('touch open tooltip err', err); }
            });
            layer._touchBound = true;
          } catch(e){ console.warn('bind layer err', e); }
        });
      } catch(e){ console.warn('bindTouchBehaviorToGeoJson err', e); }
    }

    function scanAndBind(){
      try {
        var gj = window[GJ_NAME];
        if (gj && gj.eachLayer) bindTouchBehaviorToGeoJson(gj);
        map.eachLayer(function(layer){
          try {
            if (layer && layer.eachLayer && layer.options && layer.options.name) {
              bindTouchBehaviorToGeoJson(layer);
            }
          } catch(e){}
        });
      } catch(e){ console.warn('scanAndBind err', e); }
    }

    scanAndBind();
    var retry = 0;
    var iv = setInterval(function(){
      retry++;
      scanAndBind();
      if (retry>12) clearInterval(iv);
    }, 300);

    map.on('click touchstart', function(e){
      if (openLayer) {
        try { openLayer.closeTooltip(); } catch(e){}
        openLayer = null;
      }
    });
  } catch(e){ console.warn('touch support init error', e); }
})();
</script>
"""
js_touch = js_touch_template.replace("{MAP_VAR}", map_var).replace("{GJ_VAR}", gj_var)
m.get_root().html.add_child(folium.Element(js_touch))

# -------------------------
# LayerControl and save
# -------------------------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Saved: {OUT_HTML}")