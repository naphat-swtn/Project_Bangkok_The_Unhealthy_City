"""
BKK_Hospital_Rights_CSMBS.py

Produces BKK_Hospital_Rights_CSMBS.html

Shows only hospitals that accept 'สิทธิข้าราชการ' (column "สิทธิข้าราชการ" == "YES"),
uses Hospital_CSMBS.png as marker icon, and popups show:
- ชื่อโรงพยาบาล
- เขต
- เบอร์
- เว็บไซต์
- สิทธิบัตรทอง: Yes/No
- สิทธิประกันสังคม: Yes/No
- สิทธิข้าราชการ: Yes/No

Design System follows the other views:
- Thai tile names, Bai Jamjuree font, combined district fill+stroke (#3388ff / #2c3e50),
  district tooltips, mobile tap-to-toggle tooltips, base-layer control present.
"""
import json
from pathlib import Path
import html
import base64
import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape, Point
from geopy.distance import geodesic

# -------------------------
# Config / paths
# -------------------------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"
OUT_HTML = "BKK_Hospital_Rights_CSMBS.html"

HOSP_ICON_FN = "Hospital.png"         # small inline icon for popup header (if present)
CSMBS_ICON_FN = "Hospital_CSMBS.png"  # marker icon for สิทธิข้าราชการ view
PUSH_PIN_FN = "RoundPushpin.png"

ICON_SIZE = (18, 18)
ICON_ANCHOR = (9, 9)

# -------------------------
# Helpers: prefer relative filename for marker icons; inline small popup icon if present
# -------------------------
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

CSMBS_ICON_URI = try_file_name(CSMBS_ICON_FN)
HOSP_ICON_URI = try_inline_image(HOSP_ICON_FN)
PUSH_PIN_URI = try_inline_image(PUSH_PIN_FN)

# -------------------------
# Load data
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

# Rights columns (ensure presence)
rights_cols = {
    'gold_card': 'สิทธิบัตรทอง',
    'sso': 'สิทธิประกันสังคม',
    'gov': 'สิทธิข้าราชการ'
}
for col in rights_cols.values():
    if col not in hospitals.columns:
        hospitals[col] = ""  # missing -> treat as not accepted

# -------------------------
# (Optional) compute nearest assignment (communities -> nearest hospital) to compute weight if needed
# -------------------------
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

# -------------------------
# Compute district metrics and write into geojson properties (for tooltips)
# -------------------------
district_features = bangkok_geo.get('features', [])
district_name_field = 'amp_th'  # adjust if your geojson uses a different property name

district_shapes = []
for feat in district_features:
    geom = feat.get('geometry')
    district_shapes.append(shape(geom) if geom is not None else None)

district_metrics = {}
for feat in district_features:
    nm = feat.get('properties', {}).get(district_name_field)
    district_metrics[nm] = {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0}

for h_idx, h in hospitals.iterrows():
    try:
        pt = Point(float(h[lon_col]), float(h[lat_col]))
    except Exception:
        continue
    for i, poly in enumerate(district_shapes):
        if poly is None: continue
        try:
            if poly.contains(pt):
                name = district_features[i].get('properties', {}).get(district_name_field)
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
                district_metrics[name]['num_hospitals'] += 1
                district_metrics[name]['sum_hospital_weights'] += int(h.get('weight', 0) or 0)
                break
        except Exception:
            continue

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
                district_metrics.setdefault(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
                district_metrics[name]['num_communities'] += 1
                break
        except Exception:
            continue

max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)

for feat in district_features:
    name = feat.get('properties', {}).get(district_name_field)
    metrics = district_metrics.get(name, {'num_hospitals': 0, 'num_communities': 0, 'sum_hospital_weights': 0})
    feat.setdefault('properties', {})
    feat['properties']['num_hospitals'] = metrics['num_hospitals']
    feat['properties']['num_communities'] = metrics['num_communities']
    feat['properties']['sum_hospital_weights'] = metrics['sum_hospital_weights']
    feat['properties']['choropleth_norm'] = (metrics['sum_hospital_weights'] / max_sum_weights) if max_sum_weights > 0 else 0.0

# -------------------------
# Build map (Design System)
# -------------------------
center = [float(communities[lat_col].astype(float).mean()), float(communities[lon_col].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# base tiles with Thai names
folium.TileLayer(
    tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
    name='แผนที่แบบหยาบ',
    control=True,
    show=True
).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', show=False, control=True).add_to(m)

# -------------------------
# Combined districts layer (fill + bounds) with tooltip
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
# CSMBS layer only (visible)
# -------------------------
csmbs_layer = FeatureGroup(name="Hospitals - สิทธิข้าราชการ", show=True, control=False).add_to(m)

# Filter hospitals that accept gov right (exact match 'YES' assumed)
csmbs_hospitals = hospitals[hospitals[rights_cols['gov']] == "YES"] if rights_cols['gov'] in hospitals.columns else hospitals.iloc[0:0]

def yesno(val):
    v = str(val or "").strip().upper()
    return "Yes" if v in ("YES", "Y", "TRUE", "1", "รับ") else "No"

for _, row in csmbs_hospitals.iterrows():
    try:
        latf = float(row[lat_col]); lonf = float(row[lon_col])
    except Exception:
        continue
    title = row.get('โรงพยาบาล') or row.get(hosp_name_col) or ''
    title_esc = html.escape(str(title))
    district_val = row.get('เขต') or row.get('district') or ''
    tel_val = row.get('tel') or row.get('โทรศัพท์') or ''
    url_val = row.get('url') or row.get('website') or ''

    gold_v = yesno(row.get(rights_cols['gold_card']))
    sso_v = yesno(row.get(rights_cols['sso']))
    gov_v = yesno(row.get(rights_cols['gov']))

    popup_html = f"""
    <div style="background:#EAF3FF; color:#1A1A1A; font-family: 'Bai Jamjuree', sans-serif; padding:12px; border-radius:8px; border:2px solid #6C7A89;">
      <div style="display:flex; align-items:center; gap:8px; font-weight:600; font-size:16px;">
        <img src="{HOSP_ICON_URI}" style="width:16px;height:16px;" alt="h" />
        <div>{title_esc}</div>
      </div>
      <div style="margin-top:8px; font-size:14px;">
        <div><strong>เขต:</strong> {html.escape(str(district_val))}</div>
        <div><strong>เบอร์:</strong> {html.escape(str(tel_val))}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{html.escape(str(url_val))}" target="_blank" rel="noopener noreferrer">{html.escape(str(url_val))}</a></div>
        <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
        <div><strong>สิทธิบัตรทอง:</strong> {gold_v}</div>
        <div><strong>สิทธิประกันสังคม:</strong> {sso_v}</div>
        <div><strong>สิทธิข้าราชการ:</strong> {gov_v}</div>
      </div>
    </div>
    """

    # add marker with CSMBS icon, fallback to colored circle
    try:
        icon = folium.CustomIcon(CSMBS_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon,
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title_esc).add_to(csmbs_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#66bb6a', fill=True, fill_color='#66bb6a',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(csmbs_layer)

# -------------------------
# CSS (fonts, LayerControl font, tooltip style)
# -------------------------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family: 'Bai Jamjuree', sans-serif !important; font-size: 16px !important; color: #1A1A1A !important; }
.leaflet-tooltip.district-tooltip { background:#EAF3FF; color:#1A1A1A; padding:12px; border-radius:8px; border:2px solid #6C7A89; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label {
  font-family: 'Bai Jamjuree', sans-serif !important;
  font-size: 16px !important;
}
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# -------------------------
# Inject JS to support mobile tap-to-toggle tooltips and click-to-highlight on districts
# -------------------------
gj_var = gj.get_name()
map_var = m.get_name()
js_touch_template = """
<script>
(function(){
  try {
    var isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints>0);
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

    function bindClickHighlight(gj) {
      try {
        if (!gj || !gj.eachLayer) return;
        var previous = null;
        gj.eachLayer(function(layer){
          try {
            if (layer._hasClickHandler) return;
            layer.on('click', function(e){
              if (previous && previous !== layer) {
                try { previous.setStyle({fillOpacity: (previous.defaultFillOpacity||0.2), fillColor: previous.defaultFillColor||'transparent'}); } catch(e){}
                previous = null;
              }
              try {
                if (!layer.defaultFillOpacity && layer.options) {
                  layer.defaultFillOpacity = layer.options.fillOpacity || 0.2;
                  layer.defaultFillColor = layer.options.fillColor || 'transparent';
                }
                layer.setStyle({fillColor: '#2196F3', fillOpacity: 0.35});
                previous = layer;
                if (layer.getBounds) map.fitBounds(layer.getBounds(), {padding: [20,20]});
              } catch(err){ console.warn('click highlight err', err); }
            });
            layer._hasClickHandler = true;
          } catch(e){ console.warn('bind click handler err', e); }
        });
      } catch(e){ console.warn('bindClickHighlight err', e); }
    }

    function scanAndBind(){
      try {
        var gj = window[GJ_NAME];
        if (gj && gj.eachLayer) {
          bindTouchBehaviorToGeoJson(gj);
          bindClickHighlight(gj);
        }
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
      if (retry>20) clearInterval(iv);
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
# LayerControl (base layers) and save
# -------------------------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print(f"Saved: {OUT_HTML}")