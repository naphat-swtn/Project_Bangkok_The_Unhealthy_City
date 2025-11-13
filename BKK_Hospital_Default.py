"""
BKK_Hospital_Default.py

Updated script:
- Combined districts layer (fill + bounds) with fillColor #3388ff (opacity 0.2) and stroke color #2c3e50.
- Hospital popups restored to show textual details (name, เขต, เบอร์, เว็บไซต์) — not icon-only.
- TileLayer names changed to Thai: 'แผนที่แบบหยาบ' and 'แผนที่แบบละเอียด'.
- LayerControl font set to Bai Jamjuree, 16px.
- Injected small JS to make district tooltips open/close on touch (for mobile devices).
- GeoJsonTooltip still bound for desktop hover; JS makes touch work by toggling tooltips on tap.

Usage:
    python BKK_Hospital_Default.py

Output:
    BKK_Hospital_Default.html
"""
import json
import base64
from pathlib import Path
import html
import math
import pandas as pd
import folium
from folium.features import GeoJsonTooltip, GeoJsonPopup
from folium import FeatureGroup
from shapely.geometry import shape, Point
from geopy.distance import geodesic

# -------------------------
# Config / paths
# -------------------------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
DISTRICTS_CSV = "districts.csv"   # optional
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "BKK_Hospital_Default.html"

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

# -------------------------
# Load CSVs and geojson
# -------------------------
hospitals = pd.read_csv(HOSPITALS_CSV)
communities = pd.read_csv(COMMUNITIES_CSV)
with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# -------------------------
# Detect columns, sanitize
# -------------------------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()

lat_col = 'ละติจูด'
lon_col = 'ลองจิจูด'
if lat_col not in hospitals.columns or lon_col not in hospitals.columns:
    raise KeyError("Expected hospital coords columns '{}' and '{}' in {}".format(lat_col, lon_col, HOSPITALS_CSV))
if lat_col not in communities.columns or lon_col not in communities.columns:
    raise KeyError("Expected community coords columns '{}' and '{}' in {}".format(lat_col, lon_col, COMMUNITIES_CSV))

possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

# -------------------------
# Optional: load districts.csv and map values into a dict
# -------------------------
district_click_map = {}
if Path(DISTRICTS_CSV).exists():
    try:
        districts_df = pd.read_csv(DISTRICTS_CSV)
        districts_df.columns = districts_df.columns.str.strip()
        cols = list(districts_df.columns)
        name_col = next((c for c in cols if c in ['เขต', 'name', 'district', 'amp_th']), None)
        hospcount_col = next((c for c in cols if c in ['จำนวนโรงพยาบาล', 'num_hospitals', 'hospitals']), None)
        pop_col = next((c for c in cols if c in ['จำนวนประชากร', 'จำนวนประชาชน', 'population', 'pop']), None)
        for _, r in districts_df.iterrows():
            try:
                k = str(r.get(name_col, "")).strip()
                district_click_map[k] = {
                    "num_hospitals_csv": r.get(hospcount_col, ""),
                    "population_csv": r.get(pop_col, "")
                }
            except Exception:
                continue
    except Exception:
        district_click_map = {}

# -------------------------
# Compute simple district metrics and write back into geojson properties
# (so tooltip fields exist)
# -------------------------
district_features = bangkok_geo.get('features', [])
district_name_field = 'amp_th'  # adjust if geojson uses different property name

# build shapely shapes
district_shapes = []
for feat in district_features:
    geom = feat.get('geometry')
    if geom is None:
        district_shapes.append(None)
    else:
        district_shapes.append(shape(geom))

# initialize metrics
district_metrics = {}
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    district_metrics[name] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}

# nearest assignment (communities -> nearest hospital)
comm_assigned = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[lat_col])
        comm_lon = float(comm[lon_col])
    except Exception:
        comm_assigned.append((c_idx, None, None))
        continue
    min_dist = float('inf')
    nearest_idx = None
    for h_idx, hosp in hospitals.iterrows():
        try:
            h_lat = float(hosp[lat_col])
            h_lon = float(hosp[lon_col])
        except Exception:
            continue
        d = geodesic((comm_lat, comm_lon), (h_lat, h_lon)).meters
        if d < min_dist:
            min_dist = d
            nearest_idx = h_idx
    comm_assigned.append((c_idx, nearest_idx, min_dist if min_dist != float('inf') else None))

# hospital weight (how many communities assigned)
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# assign hospitals to district (PIP)
for h_idx, h in hospitals.iterrows():
    try:
        pt = Point(float(h[lon_col]), float(h[lat_col]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None:
            continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                if name not in district_metrics:
                    district_metrics[name] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}
                district_metrics[name]['num_hospitals'] += 1
                district_metrics[name]['sum_hospital_weights'] += int(h.get('weight', 0) or 0)
                break
        except Exception:
            continue

# assign communities to district
for c_idx, c in communities.iterrows():
    try:
        pt = Point(float(c[lon_col]), float(c[lat_col]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None:
            continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                if name not in district_metrics:
                    district_metrics[name] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}
                district_metrics[name]['num_communities'] += 1
                break
        except Exception:
            continue

max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

# write into geojson properties
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / max_sum_weights) if max_sum_weights > 0 else 0.0
    # also embed optional csv values (if present)
    extra = district_click_map.get(name, {})
    feat['properties']['num_hospitals_csv'] = extra.get('num_hospitals_csv', '')
    feat['properties']['population_csv'] = extra.get('population_csv', '')

# -------------------------
# Create map
# -------------------------
center = [float(communities[lat_col].astype(float).mean()), float(communities[lon_col].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# base tiles (names changed to Thai)
folium.TileLayer(
    tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
    name='แผนที่แบบหยาบ',
    control=True,
    show=True
).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', show=False, control=True).add_to(m)

# -------------------------
# Districts: single GeoJson layer for both fill and stroke + tooltip
# fill: #3388ff with opacity 0.2; stroke: #2c3e50
# -------------------------
def combined_district_style(feature):
    return {
        'fillColor': '#3388ff',
        'color': '#2c3e50',   # stroke color as requested
        'weight': 3,
        'fillOpacity': 0.2,
        'opacity': 0.95
    }

districts_layer = FeatureGroup(name="Districts (fill + bounds)", show=True, control=False).add_to(m)

# Use GeoJsonTooltip to bind tooltip on hover to the same GeoJson layer (desktop)
tooltip_fields = [district_name_field, 'num_hospitals', 'num_communities']
tooltip_aliases = ['เขต:', 'จำนวนโรงพยาบาล:', 'จำนวนชุมชน:']
gj = folium.GeoJson(
    data=bangkok_geo,
    style_function=combined_district_style,
    tooltip=GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases, localize=True, labels=True, sticky=True),
    name="Districts Combined"
).add_to(districts_layer)

# -------------------------
# Hospitals markers (popup restored to detailed text info)
# -------------------------
hospitals_layer = FeatureGroup(name="Hospitals Only", show=True, control=False).add_to(m)
ICON_SIZE = (22, 22)
ICON_ANCHOR = (11, 11)
for _, row in hospitals.iterrows():
    try:
        latf = float(row[lat_col])
        lonf = float(row[lon_col])
    except Exception:
        continue
    title = row.get('โรงพยาบาล') or row.get(hosp_name_col) or ''
    title_esc = html.escape(str(title))
    district_val = row.get('เขต') or row.get('district') or ''
    tel_val = row.get('tel') or row.get('โทรศัพท์') or ''
    url_val = row.get('url') or row.get('website') or ''

    # RESTORED popup: textual details (icon in header + text details) instead of icon-only popup
    popup_html = """
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:600; font-size:16px;">
        <img src="{icon}" style="width:16px;height:16px;" alt="h" />
        <div>{title}</div>
      </div>
      <div style="margin-top:8px; font-size:14px;">
        <div><strong>เขต:</strong> {district}</div>
        <div><strong>เบอร์:</strong> {tel}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></div>
      </div>
    </div>
    """.format(icon=HOSP_ICON_URI, title=title_esc, district=html.escape(str(district_val)),
               tel=html.escape(str(tel_val)), url=html.escape(str(url_val)))

    folium.Marker(location=[latf, lonf],
                  icon=folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR),
                  popup=folium.Popup(popup_html, max_width=360),
                  tooltip=title_esc).add_to(hospitals_layer)

# -------------------------
# Simple CSS to ensure font and tooltip size + LayerControl font
# -------------------------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; }

/* LayerControl font (ชื่อ map และรายการ) */
.leaflet-control-layers,
.leaflet-control-layers .leaflet-control-layers-list,
.leaflet-control-layers label {
  font-family: 'Bai Jamjuree', sans-serif !important;
  font-size: 16px !important;
  line-height: 1.2 !important;
}

/* make inputs bigger for touch */
.leaflet-control-layers input[type="checkbox"],
.leaflet-control-layers input[type="radio"] {
  width: 18px;
  height: 18px;
  margin-top: 2px;
}

/* hospital popup base */
.hospital-popup { background:#EAF3FF; color:#1A1A1A; font-family:'Bai Jamjuree',sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# -------------------------
# Inject JS to enable touch behavior: tap to open/close GeoJson tooltips on mobile
# This makes tooltips usable on touch devices while keeping hover on desktop.
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
                // close previously opened tooltip (if different)
                if (openLayer && openLayer !== this) {
                  try { openLayer.closeTooltip(); } catch(err){}
                  openLayer = null;
                }
                // toggle this tooltip
                var already = (openLayer === this);
                if (already) {
                  try { this.closeTooltip(); } catch(err){}
                  openLayer = null;
                } else {
                  try { this.openTooltip(e.latlng); } catch(err){}
                  openLayer = this;
                }
              } catch(err){
                console.warn('touch open tooltip err', err);
              }
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
        // Also try to find GeoJSON objects in map layers if nested in FeatureGroups
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

    // close tooltip when tapping map background
    try {
      map.on('click touchstart', function(e){
        if (openLayer) {
          try { openLayer.closeTooltip(); } catch(e){}
          openLayer = null;
        }
      });
    } catch(e){}
  } catch(e){ console.warn('touch support init error', e); }
})();
</script>
"""
js_touch = (js_touch_template
            .replace("{MAP_VAR}", map_var)
            .replace("{GJ_VAR}", gj_var))
m.get_root().html.add_child(folium.Element(js_touch))

# -------------------------
# LayerControl (base layers only) and save
# -------------------------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print("Saved:", OUT_HTML)
print("- Combined 'Districts (fill + bounds)' with stroke color #2c3e50.")
print("- Hospital popups restored to show textual details.")
print("- Tile names changed to Thai and LayerControl font set to Bai Jamjuree 16px.")
print("- Mobile touch support injected for GeoJson tooltips (tap to open/close).")