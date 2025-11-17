# -*- coding: utf-8 -*-
"""
BKK_Hospital_Distance_Default.py

Embedded districts (fill + bounds), hospital and community markers.

Changes in this version:
- Community popup font sizes matched to hospital popup (header 16px, body 14px).
- Hospital popup now shows Hospital.png icon before the hospital name.
- Community popup now shows House.png icon before the community name.
- Keeps districts embedded, tooltips bound, and district layer moved to back so markers are clickable.

Usage:
  python BKK_Hospital_Distance_Default.py
  python -m http.server 8000
  Open http://localhost:8000/BKK_Hospital_Distance_Default.html
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

# ---------- Config ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
DISTRICTS_SRC = "districts.geojson"   # must exist (read-only)
OUT_HTML = "BKK_Hospital_Distance_Default.html"
HOSP_ICON_FN = "Hospital.png"
HOUSE_ICON_FN = "House.png"   # new icon for community popup

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'

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

# ---------- Load CSVs ----------
if not Path(HOSPITALS_CSV).exists() or not Path(COMMUNITIES_CSV).exists():
    raise SystemExit("Missing hospitals.csv or communities.csv in the working directory.")

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
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col, 0), errors='coerce').fillna(0).astype(int)

# ensure hospital numeric fields exist
near_pop_col = "จำนวนประชากรใกล้เคียงที่ต้องรองรับ"
beds_col = "จำนวนเตียง"
hospitals[near_pop_col] = pd.to_numeric(hospitals.get(near_pop_col, 0), errors='coerce').fillna(0).astype(int)
hospitals[beds_col] = pd.to_numeric(hospitals.get(beds_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Read districts.geojson ----------
dist_path = Path(DISTRICTS_SRC)
if not dist_path.exists():
    raise SystemExit(f"{DISTRICTS_SRC} not found - please add the file to working dir.")

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

# ---------- Compute nearest hospital for each community ----------
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

# hospital weights
hospitals = hospitals.copy()
hospitals['weight'] = 0
for c_idx, h_idx, d in comm_assigned:
    if h_idx is not None and pd.notnull(h_idx):
        try:
            hospitals.at[h_idx, 'weight'] += 1
        except Exception:
            pass

# ---------- Compute district metrics by spatial assignment ----------
district_metrics = {}
for name in district_names:
    district_metrics[name] = {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0}

# assign hospitals
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
                m['sum_hospital_weights'] += int(hosp.get('weight',0) or 0)
                break
        except Exception:
            continue

# assign communities
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

# normalization
max_sum_weights = max((v['sum_hospital_weights'] for v in district_metrics.values()), default=1)
for v in district_metrics.values():
    v['_global_max_sum'] = max_sum_weights

# ---------- Build modified features (in-memory) ----------
out_features = []
for i, feat in enumerate(district_features):
    props = feat.get('properties', {}) or {}
    name = props.get(district_name_field) if district_name_field else None
    injected = None
    if name in district_metrics:
        injected = district_metrics[name]
    else:
        injected = {'num_hospitals':0,'num_communities':0,'sum_hospital_weights':0}
        geom = feat.get('geometry')
        if geom:
            try:
                shp = shape(geom)
                centroid = shp.centroid
                for j, p in enumerate(district_shapes):
                    if p is None: continue
                    try:
                        if p.contains(centroid) or p.intersects(centroid):
                            maybe = district_names[j]
                            if maybe in district_metrics:
                                injected = district_metrics[maybe]
                                break
                    except Exception:
                        continue
            except Exception:
                pass
    props['district_name'] = name or (props.get('name') or props.get('NAME') or '—')
    # copy to common keys so tooltip field lookup succeeds in all variants
    props['amp_th'] = props['district_name']
    props['name'] = props['district_name']
    props['num_hospitals'] = int(injected.get('num_hospitals', 0))
    props['num_communities'] = int(injected.get('num_communities', 0))
    props['sum_hospital_weights'] = int(injected.get('sum_hospital_weights', 0))
    out_features.append({"type":"Feature","geometry":feat.get('geometry'), "properties":props})

# choropleth_norm
global_max = max((f['properties'].get('sum_hospital_weights',0) for f in out_features), default=1)
for f in out_features:
    s = f['properties'].get('sum_hospital_weights', 0)
    f['properties']['choropleth_norm'] = float(s) / float(global_max) if global_max > 0 else 0.0

# ---------- Build folium map and EMBED the modified geojson (combined fill + stroke) ----------
center = [float(communities[LAT_COL].astype(float).mean()), float(communities[LON_COL].astype(float).mean())]
m = folium.Map(location=center, zoom_start=12, tiles=None)

# base tiles (Thai names)
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# Create a FeatureGroup for districts and hide it from LayerControl (control=False)
districts_fg = FeatureGroup(name="Districts (fill + bounds)", show=True, control=False).add_to(m)

# combined fill+stroke GeoJson (added into the FeatureGroup so it won't appear in LayerControl)
# IMPORTANT: include interactive True so polygons receive pointer events
district_gj = folium.GeoJson(
    data={"type":"FeatureCollection","features":out_features},
    style_function=lambda feat: {
        'fillColor': '#3388ff',
        'color': '#2c3e50',
        'weight': 3,
        'fillOpacity': 0.2,
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

# Add a stroke-only layer on top but inside the same FeatureGroup (hidden from LayerControl)
# also interactive true
bounds_gj = folium.GeoJson(
    data={"type":"FeatureCollection","features":out_features},
    style_function=lambda feat: {'fillColor':'transparent','color':'#2c3e50','weight':2.6,'opacity':0.95,'interactive': True}
).add_to(districts_fg)

# ---------- Hospitals ----------
hosp_layer = FeatureGroup(name="All Hospitals", show=True, control=False).add_to(m)
HOSP_ICON_URI = try_file_name(HOSP_ICON_FN)
HOUSE_ICON_URI = try_file_name(HOUSE_ICON_FN)
ICON_SIZE = (22,22); ICON_ANCHOR = (11,11)
for _, row in hospitals.iterrows():
    try:
        latf = float(row[LAT_COL]); lonf = float(row[LON_COL])
    except Exception:
        continue
    title = row.get('โรงพยาบาล') or row.get(hosp_name_col) or ''
    title_esc = html.escape(str(title))
    district_val = row.get('เขต') or row.get('district') or ''
    weight = int(row.get('weight', 0))
    near_pop = int(row.get(near_pop_col, 0))
    beds = int(row.get(beds_col, 0))
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:12px;border-radius:8px;border:2px solid #6C7A89;max-width:360px;">
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
        icon = folium.CustomIcon(HOSP_ICON_URI, ICON_SIZE, ICON_ANCHOR)
        folium.Marker(location=[latf, lonf], icon=icon, popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(hosp_layer)
    except Exception:
        folium.CircleMarker(location=[latf, lonf], radius=6, color='#d32f2f', fill=True, fill_color='#d32f2f',
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title_esc).add_to(hosp_layer)

# ---------- Communities ----------
comm_layer = FeatureGroup(name="Communities", show=True, control=False).add_to(m)
for comm_idx, nearest_idx, dist_m in comm_assigned:
    comm = communities.loc[comm_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
    except Exception:
        continue
    comm_name = comm.get(comm_name_col,"")
    comm_pop = int(comm.get(comm_pop_col,0))
    if nearest_idx is not None and pd.notnull(nearest_idx):
        hosp = hospitals.loc[nearest_idx]
        hosp_name = hosp.get(hosp_name_col,"")
        dist_text = f"{dist_m:.0f} m" if dist_m is not None else "N/A"
    else:
        hosp_name = "N/A"; dist_text = "N/A"
    # community popup now uses the same header font-size and includes house icon
    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:320px;">
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;">
        <img src="{HOUSE_ICON_URI}" style="width:16px;height:16px;" alt="house" />
        <div>{html.escape(str(comm_name))}</div>
      </div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>โรงพยาบาลใกล้ที่สุด:</strong> {html.escape(str(hosp_name))}</div>
        <div><strong>ระยะ:</strong> {dist_text}</div>
        <div><strong>ประชากร:</strong> {comm_pop}</div>
      </div>
    </div>
    """
    folium.CircleMarker(location=[clat, clon], radius=4.5, color='#1976d2', fill=True, fill_color='#1976d2',
                        fill_opacity=0.95, popup=folium.Popup(popup_html, max_width=360), tooltip=str(comm_name)).add_to(comm_layer)

# ---------- Connections ----------
conn_layer = FeatureGroup(name="Connections (community → hospital)", show=True, control=False).add_to(m)
for comm_idx, nearest_idx, dist_m in comm_assigned:
    if nearest_idx is None or pd.isna(nearest_idx):
        continue
    comm = communities.loc[comm_idx]; hosp = hospitals.loc[nearest_idx]
    try:
        clat = float(comm[LAT_COL]); clon = float(comm[LON_COL])
        hlat = float(hosp[LAT_COL]); hlon = float(hosp[LON_COL])
    except Exception:
        continue
    folium.PolyLine(locations=[[clat, clon],[hlat, hlon]], color='#2196F3', weight=1.2, opacity=0.6).add_to(conn_layer)

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:8px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:16px !important; line-height:1.2 !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- JS: ensure district GeoJSON is behind markers, bind click-to-fit highlight,
# and bind mouseover/mouseout (open/close tooltip) robustly ----------
district_var = district_gj.get_name()
bounds_var = bounds_gj.get_name()
map_var = m.get_name()

# Build JS template and replace placeholders to avoid f-string braces issues
js_template = """
<script>
(function(){
  try {
    var map = MAP_VAR;
    var gj = DIST_GJ_VAR;
    var bounds = BOUNDS_GJ_VAR;
    console.log("district binder init, gj:", !!gj, "bounds:", !!bounds);

    function reorder(){
      try {
        if (bounds && bounds.bringToBack) bounds.bringToBack();
        if (gj && gj.bringToBack) gj.bringToBack();
      } catch(e) { console.warn('reorder err', e); }
    }
    // run a few times to be robust against load ordering
    setTimeout(reorder, 50);
    setTimeout(reorder, 300);
    setTimeout(reorder, 1000);

    function bindLayerEvents(layer){
      try {
        // ensure tooltip exists: if none, create one from properties
        if (!layer.getTooltip || !layer.bindTooltip) {
          return;
        }
        var hasTooltip = !!layer.getTooltip();
        if (!hasTooltip && layer.feature && layer.feature.properties) {
          var p = layer.feature.properties;
          var name = p['district_name'] || p['amp_th'] || p['name'] || '—';
          var numh = p['num_hospitals'] || 0;
          var numc = p['num_communities'] || 0;
          var html = "<div style='font-family: \"Bai Jamjuree\", sans-serif; font-size:15px; padding:6px; background:#EAF3FF; border:2px solid #6C7A89; border-radius:8px; color:#1A1A1A;'><div style='font-weight:700;'>" + name + "</div><div>จำนวนโรงพยาบาล: " + numh + "</div><div>จำนวนชุมชน: " + numc + "</div></div>";
          try { layer.bindTooltip(html, {sticky:true, direction:'auto', permanent:false}); } catch(e){ console.warn('bindTooltip err', e); }
        }

        // click-to-highlight (persistent)
        layer.on('click', function(e){
          try {
            if (window._lastDistrict && window._lastDistrict !== this) {
              try { window._lastDistrict.setStyle({color: window._lastDistrict.origColor || '#2c3e50', weight: window._lastDistrict.origWeight || 3, fillOpacity: window._lastDistrict.origFillOpacity || 0.2}); } catch(e){}
            }
            if (!this.origColor) { this.origColor = this.options.color; this.origWeight = this.options.weight; this.origFillOpacity = this.options.fillOpacity; }
            this.setStyle({color:'#000000', weight:4, fillOpacity:0.35});
            window._lastDistrict = this;
            if (this.getBounds) map.fitBounds(this.getBounds(), {padding:[20,20]});
          } catch(err){ console.warn(err); }
        });

        // mouseover -> open tooltip
        layer.on('mouseover', function(e){
          try { if (this.openTooltip) this.openTooltip(e.latlng); } catch(e){}
        });
        // mouseout -> close tooltip
        layer.on('mouseout', function(e){
          try { if (this.closeTooltip) this.closeTooltip(); } catch(e){}
        });

      } catch(e){ console.warn('bindLayerEvents err', e); }
    }

    // bind events for existing layers
    if (gj && gj.eachLayer) {
      gj.eachLayer(function(layer){
        bindLayerEvents(layer);
      });
      console.log('bound events to gj layers');
    } else {
      console.warn('no gj layer found to bind');
    }

    // also scan for nested featuregroups (in case)
    map.eachLayer(function(l){
      try {
        if (l && l.eachLayer && l.options && l.options.pane) {
          // if layer contains GeoJSON children, bind them too
          l.eachLayer(function(child){
            try { bindLayerEvents(child); } catch(e){}
          });
        }
      } catch(e){}
    });

  } catch(e){ console.warn('init err', e); }
})();
</script>
"""

js = js_template.replace("MAP_VAR", map_var).replace("DIST_GJ_VAR", district_var).replace("BOUNDS_GJ_VAR", bounds_var)
m.get_root().html.add_child(folium.Element(js))

# ---------- LayerControl (only base maps shown) and save ----------
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)
print("Saved:", OUT_HTML)
print("Districts embedded (fill + bounds) hidden from LayerControl; district layers moved to back and tooltips bound for hover.")