# BKK_Hospital_Default.py
"""
Build a lightweight BKK_Hospital_Default.html + external districts.geojson and external icons.
This script:
- Writes a minified districts.geojson (OUT_GEOJSON) instead of embedding the large GeoJSON into HTML.
- Does NOT inline icon images; it references local image files (Hospital.png, RoundPushpin.png) if present.
- Keeps visible behavior identical: district fill, district bounds (stroke #2c3e50), hover tooltips,
  click popups on districts, hospital markers + detailed popups.
- Injects client-side JS that fetches the external districts.geojson and creates the district layers
  (fill + bounds) and binds tooltips in the same style as the original.
Usage:
  - Put this script in the same folder as hospitals.csv, communities.csv, districts_bangkok.geojson,
    Hospital.png, RoundPushpin.png (icons are optional).
  - Run: python BKK_Hospital_Default.py
  - This produces: BKK_Hospital_Default.html (smaller) and districts.geojson (external, may be large).
  - To view locally use: python -m http.server 8000  then open http://localhost:8000/BKK_Hospital_Default.html
"""
import json
from pathlib import Path
import html
import pandas as pd
import folium
from folium import FeatureGroup
from shapely.geometry import shape, Point
from geopy.distance import geodesic

# -------------------------
# Config / paths
# -------------------------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_SRC = "districts_bangkok.geojson"   # original geojson (source)
OUT_HTML = "BKK_Hospital_Default.html"
OUT_GEOJSON = "districts.geojson"           # external geojson written by this script

HOSP_ICON_FN = "Hospital.png"
PUSH_PIN_FN = "RoundPushpin.png"

# -------------------------
# Helpers: prefer local file path for icons (do NOT inline)
# -------------------------
def prefer_file_path(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

HOSP_ICON_URI = prefer_file_path(HOSP_ICON_FN)
PUSH_PIN_URI = prefer_file_path(PUSH_PIN_FN)

# -------------------------
# Load data
# -------------------------
hospitals = pd.read_csv(HOSPITALS_CSV)
communities = pd.read_csv(COMMUNITIES_CSV)
with open(GEOJSON_SRC, "r", encoding="utf-8") as f:
    bangkok_geo = json.load(f)

# optional districts.csv mapping (embed into geojson properties)
DISTRICTS_CSV = "districts.csv"
district_click_map = {}
if Path(DISTRICTS_CSV).exists():
    try:
        ddf = pd.read_csv(DISTRICTS_CSV)
        ddf.columns = ddf.columns.str.strip()
        cols = list(ddf.columns)
        name_col = next((c for c in cols if c in ['เขต', 'name', 'district', 'amp_th']), None)
        hospcount_col = next((c for c in cols if c in ['จำนวนโรงพยาบาล', 'num_hospitals', 'hospitals']), None)
        pop_col = next((c for c in cols if c in ['จำนวนประชากร', 'จำนวนประชาชน', 'population', 'pop']), None)
        for _, r in ddf.iterrows():
            k = str(r.get(name_col, "")).strip()
            district_click_map[k] = {
                "num_hospitals_csv": r.get(hospcount_col, ""),
                "population_csv": r.get(pop_col, "")
            }
    except Exception:
        district_click_map = {}

# -------------------------
# Sanitize columns / detect names
# -------------------------
hospitals.columns = hospitals.columns.str.strip()
communities.columns = communities.columns.str.strip()
possible_hosp_name_cols = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name_cols if c in hospitals.columns), hospitals.columns[0])

lat_col = 'ละติจูด'
lon_col = 'ลองจิจูด'
if lat_col not in hospitals.columns or lon_col not in hospitals.columns:
    raise KeyError(f"Expected hospital coords columns '{lat_col}', '{lon_col}' in {HOSPITALS_CSV}")
if lat_col not in communities.columns or lon_col not in communities.columns:
    raise KeyError(f"Expected community coords columns '{lat_col}', '{lon_col}' in {COMMUNITIES_CSV}")

# -------------------------
# Compute and embed district metrics into geojson properties
# (so client JS can read name/num_hospitals/etc)
district_name_field = 'amp_th'
district_features = bangkok_geo.get("features", [])

# build shapely shapes for PIP tests
district_shapes = []
for feat in district_features:
    geom = feat.get("geometry")
    district_shapes.append(shape(geom) if geom is not None else None)

district_metrics = {}
for feat in district_features:
    nm = feat.get('properties', {}).get(district_name_field)
    district_metrics[nm] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}

# compute nearest hospital per community for weight
comm_assigned = []
for c_idx, comm in communities.iterrows():
    try:
        comm_lat = float(comm[lat_col]); comm_lon = float(comm[lon_col])
    except Exception:
        comm_assigned.append((c_idx, None, None)); continue
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

hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# assign hospitals -> district
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
                district_metrics.setdefault(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                district_metrics[name]['num_hospitals'] += 1
                district_metrics[name]['sum_hospital_weights'] += int(h.get('weight',0) or 0)
                break
        except Exception:
            continue

# assign communities -> district
for c_idx, c in communities.iterrows():
    try:
        pt = Point(float(c[lon_col]), float(c[lat_col]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None: continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                district_metrics.setdefault(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
                district_metrics[name]['num_communities'] += 1
                break
        except Exception:
            continue

max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

# write metrics and optional csv values into properties
for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights']/max_sum_weights) if max_sum_weights>0 else 0.0
    extra = district_click_map.get(name, {})
    feat['properties']['num_hospitals_csv'] = extra.get('num_hospitals_csv', '')
    feat['properties']['population_csv'] = extra.get('population_csv', '')

# -------------------------
# Save external (minified) geojson
with open(OUT_GEOJSON, "w", encoding="utf-8") as fo:
    json.dump(bangkok_geo, fo, ensure_ascii=False, separators=(",", ":"))
print(f"Wrote external geojson: {OUT_GEOJSON} ({Path(OUT_GEOJSON).stat().st_size/1e6:.2f} MB)")

# -------------------------
# Build folium map (do NOT embed geojson). Keep hospitals added by folium on server side.
center = [float(communities[lat_col].astype(float).mean()), float(communities[lon_col].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# Base tiles (names can be changed)
folium.TileLayer(
    tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
    name='CartoDB Positron',
    control=True,
    show=True
).add_to(m)
folium.TileLayer('OpenStreetMap', name='OpenStreetMap', show=False, control=True).add_to(m)

# Hospitals layer (folium markers) - remain embedded in HTML (small)
hospitals_layer = FeatureGroup(name="Hospitals Only", show=True, control=False).add_to(m)
ICON_SIZE = (22, 22)
ICON_ANCHOR = (11, 11)
for _, row in hospitals.iterrows():
    try:
        latf = float(row[lat_col]); lonf = float(row[lon_col])
    except Exception:
        continue
    hosp_display_name = row.get('โรงพยาบาล') or row.get(hosp_name_col) or ''
    hosp_title_esc = html.escape(str(hosp_display_name))
    district_val = row.get('เขต') or row.get('district') or ''
    tel_val = row.get('tel') or row.get('โทรศัพท์') or ''
    url_val = row.get('url') or row.get('website') or ''
    district_esc = html.escape(str(district_val))
    tel_esc = html.escape(str(tel_val))
    url_esc = html.escape(str(url_val))

    popup_html = f"""
    <div class="hospital-popup">
      <div class="hp-header">
        <img src="{HOSP_ICON_URI}" class="hp-icon" />
        <div class="hp-title">{hosp_title_esc}</div>
      </div>
      <div class="hp-desc">
        <div><strong>เขต:</strong> {district_esc}</div>
        <div><strong>เบอร์:</strong> {tel_esc}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{url_esc}" target="_blank" rel="noopener noreferrer">{url_esc}</a></div>
      </div>
    </div>
    """
    folium.Marker(location=[latf, lonf],
                  icon=folium.CustomIcon(icon_image=HOSP_ICON_URI, icon_size=ICON_SIZE, icon_anchor=ICON_ANCHOR),
                  popup=folium.Popup(popup_html, max_width=360),
                  tooltip=hosp_title_esc).add_to(hospitals_layer)

# -------------------------
# Inject CSS (fonts + tooltip/popup styling)
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; }
.leaflet-tooltip.district-tooltip { background: #EAF3FF; color: #1A1A1A; font-family: 'Bai Jamjuree', sans-serif; font-size: 16px; padding: 15px; border-radius: 10px; border: 3px solid #6C7A89; box-shadow: none; display:inline-flex; flex-direction:column; gap:10px; align-items:flex-start; max-width:320px; }
.leaflet-tooltip.district-tooltip .dt-header { display:flex; align-items:center; gap:8px; font-weight:600; font-size:16px; }
.leaflet-tooltip.district-tooltip .dt-header img { width:16px; height:16px; }
.leaflet-tooltip.district-tooltip .dt-desc { font-size:14px; line-height:1.4; color:#1A1A1A; }
.hospital-popup { background: #EAF3FF; color: #1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding: 15px; border-radius: 10px; border: 3px solid #6C7A89; width: 100%; box-sizing: border-box; }
.hospital-popup .hp-header { display:flex; align-items:center; gap:8px; font-weight:600; font-size:16px; margin-bottom:10px; }
.hospital-popup .hp-icon { width:16px; height:16px; }
.hospital-popup .hp-desc { font-size:14px; line-height:1.4; }
.hospital-popup a { color: #1A1A1A; text-decoration: underline; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# -------------------------
# Inject client-side JS: fetch external geojson and add both fill and bounds layers,
# bind tooltips to the bounds layer exactly like original behavior.
map_var = m.get_name()
js_template = """
<script>
(function(){
  var map = {MAP_VAR};
  var geojsonUrl = {OUT_GEOJSON};
  var pushPin = {PUSH_PIN_URI};

  fetch(geojsonUrl).then(function(r){ return r.json(); }).then(function(geojson) {
    // fill layer (light blue)
    var fillStyle = function(feature) {
      return { fillColor: '#EAF3FF', color: '#6C7A89', weight: 3, fillOpacity: 0.6, opacity: 0.95 };
    };
    var fillLayer = L.geoJSON(geojson, { style: fillStyle });
    fillLayer.addTo(map);

    // bounds layer (stroke) - interactive and gets tooltips
    var boundsStyle = function(feature) {
      return { fillColor: 'transparent', color: '#2c3e50', weight: 3.0, opacity: 0.95 };
    };
    var boundsLayer = L.geoJSON(geojson, {
      style: boundsStyle,
      onEachFeature: function(feature, layer) {
        try {
          if (layer && layer._path && layer._path.style) layer._path.style.pointerEvents = '';
          if (layer.options) layer.options.interactive = true;
          var props = (feature && feature.properties) ? feature.properties : {};
          var name = props['""" + district_name_field + """'] || props['name'] || '';
          var numh = props['num_hospitals_csv'] || props['num_hospitals'] || '';
          var popv = props['population_csv'] || props['population'] || '';
          var html = '<div class="dt-header"><img src="'+ (pushPin || '') + '" alt="pin" style="width:16px;height:16px;margin-right:8px;"/><div>' + (name||'') + '</div></div>';
          html += '<div class="dt-desc"><div><strong>จำนวนโรงพยาบาล:</strong> '+ (numh||'') +'</div>';
          html += '<div><strong>จำนวนประชากร:</strong> '+ (popv||'') +'</div></div>';
          layer.bindTooltip(html, {className: 'district-tooltip', direction: 'top', sticky: true, offset: [0, -10]});
          layer.on('mouseover', function(e){ try { this.openTooltip(); } catch(e){}; });
          layer.on('mouseout', function(e){ try { this.closeTooltip(); } catch(e){}; });
          layer.on('click', function(e){
            try {
              var popupHtml = '<div style="font-family: Bai Jamjuree, sans-serif;"><b>' + (name||'') + '</b><br/>จำนวนโรงพยาบาล: ' + (numh||'') + '<br/>จำนวนประชากร: ' + (popv||'') + '</div>';
              L.popup().setLatLng(e.latlng).setContent(popupHtml).openOn(map);
            } catch(err){ console.warn('district click popup err', err); }
          });
        } catch(err){ console.warn('onEachFeature client err', err); }
      }
    });
    boundsLayer.addTo(map);

    // expose for debugging if needed
    window.__bkk_fillLayer = fillLayer;
    window.__bkk_boundsLayer = boundsLayer;
    console.log('External geojson loaded and layers added:', geojsonUrl);
  }).catch(function(err){
    console.warn('Failed to load external geojson:', err);
  });
})();
</script>
"""
# safe replace placeholders
js = js_template.replace("{MAP_VAR}", map_var).replace("{OUT_GEOJSON}", json.dumps(OUT_GEOJSON)).replace("{PUSH_PIN_URI}", json.dumps(PUSH_PIN_URI))
m.get_root().html.add_child(folium.Element(js))

# -------------------------
# LayerControl (base layers only) and save
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Saved: {OUT_HTML}")
print(f"External geojson: {OUT_GEOJSON} (place this file together with the HTML when serving).")
print("Open via HTTP server (python -m http.server) or host on GitHub Pages / Netlify for fetch to work.")