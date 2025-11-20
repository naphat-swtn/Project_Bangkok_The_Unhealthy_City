# -*- coding: utf-8 -*-
"""
Bangchanpattana_Community_Default.py

Generate Bangchanpattana_Community_Default.html — focused view that shows only the
community named "ชุมชนบางชันพัฒนา" and the nearest hospitals for four categories:
  - nearest hospital (any)
  - nearest hospital that accepts สิทธิบัตรทอง (UHC)
  - nearest hospital that accepts สิทธิประกันสังคม (SSS)
  - nearest hospital that accepts สิทธิข้าราชการ (CSMBS)

Adjustments per user:
- Hospital popups list which rights each hospital accepts (Yes/No for UHC/SSS/CSMBS).
- Community popup shows district and population (no coordinates).
- The layer filter entries "Nearest hospital (any) / Nearest UHC hospital / Nearest SSS hospital / Nearest CSMBS hospital / Connections"
  are not shown in the LayerControl (they still exist on the map but are not presented as toggles).

Output: Bangchanpattana_Community_Default.html

Usage:
    python Bangchanpattana_Community_Default.py
"""
import json
from pathlib import Path
import html
import math

import pandas as pd
import folium
from folium import FeatureGroup
from folium.features import GeoJsonTooltip
from shapely.geometry import shape, Point as ShapelyPoint
from geopy.distance import geodesic

# ---------- Config / paths ----------
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
DISTRICTS_SRC = "districts_bangkok.geojson"   # optional but used to highlight district if available
OUT_HTML = "Bangchanpattana_Community_Default.html"

HOSP_ICON_FN = "Hospital.png"
HOUSE_ICON_FN = "House.png"

LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'
TARGET_COMMUNITY_NAME = "ชุมชนบางชันพัฒนา"

ICON_SIZE = (20, 20)
ICON_ANCHOR = (10, 10)

# ---------- Helpers ----------
def try_file_name(path):
    p = Path(path)
    return str(p.name) if p.exists() else path

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

def esc(s):
    return html.escape(str(s)) if s is not None else ''

# helper to produce Yes/No for rights
def rights_yesno(row, col, keywords=()):
    if col:
        return "Yes" if truthy(row.get(col, "")) else "No"
    # fallback: search note-like columns for keywords (case-insensitive)
    for c in row.index:
        if c.lower() in ('note','notes','type','remark','comment'):
            v = str(row.get(c, "") or "")
            for kw in keywords:
                if kw.lower() in v.lower():
                    return "Yes"
    return "No"

# ---------- Load data ----------
for p in (HOSPITALS_CSV, COMMUNITIES_CSV):
    if not Path(p).exists():
        raise SystemExit(f"Missing required file: {p}")

hospitals = pd.read_csv(HOSPITALS_CSV).rename(columns=lambda c: c.strip())
communities = pd.read_csv(COMMUNITIES_CSV).rename(columns=lambda c: c.strip())

# detect name columns
possible_hosp_name = ['โรงพยาบาล', 'โรงพาบาล', 'ชื่อโรงพยาบาล', 'hospital', 'name', 'ชื่อ']
hosp_name_col = next((c for c in possible_hosp_name if c in hospitals.columns), hospitals.columns[0])
possible_comm_name = ['ชุมชน', 'ชื่อชุมชน', 'community', 'name', 'ชื่อ']
comm_name_col = next((c for c in possible_comm_name if c in communities.columns), communities.columns[0])

# rights column detection (best-effort)
uhc_candidates = ['รับสิทธิบัตรทอง','สิทธิบัตรทอง','UHC','gold_card','รับ_uc','accept_uhc']
sss_candidates = ['สิทธิประกันสังคม','ประกันสังคม','SSS','social_security','รับ_sss','accept_sss']
csmbs_candidates = ['สิทธิข้าราชการ','ข้าราชการ','CSMBS','csmbs','รับข้าราชการ','accept_csmbs']

uhc_col = detect_rights_column(hospitals.columns, uhc_candidates)
sss_col = detect_rights_column(hospitals.columns, sss_candidates)
csmbs_col = detect_rights_column(hospitals.columns, csmbs_candidates)

# ensure numeric coords exist
if LAT_COL not in hospitals.columns or LON_COL not in hospitals.columns:
    raise KeyError(f"Hospital coords columns '{LAT_COL}'/'{LON_COL}' not found in {HOSPITALS_CSV}")
if LAT_COL not in communities.columns or LON_COL not in communities.columns:
    raise KeyError(f"Community coords columns '{LAT_COL}'/'{LON_COL}' not found in {COMMUNITIES_CSV}")

# community population col detection (for popup)
possible_pop_cols = ['จำนวนประชากร','population','pop','จำนวนประชาชน','ประชากร']
comm_pop_col = next((c for c in possible_pop_cols if c in communities.columns), None)
if comm_pop_col is None:
    communities['population'] = 0
    comm_pop_col = 'population'
else:
    communities[comm_pop_col] = pd.to_numeric(communities.get(comm_pop_col, 0), errors='coerce').fillna(0).astype(int)

# ---------- Find target community ----------
target_rows = communities[communities[comm_name_col].astype(str).str.strip() == TARGET_COMMUNITY_NAME]
if target_rows.empty:
    # try case-insensitive match
    target_rows = communities[communities[comm_name_col].astype(str).str.strip().str.lower() == TARGET_COMMUNITY_NAME.lower()]

if target_rows.empty:
    raise SystemExit(f"Could not find community named '{TARGET_COMMUNITY_NAME}' in {COMMUNITIES_CSV}")

# take the first matching community
comm_idx = target_rows.index[0]
comm_row = communities.loc[comm_idx]
comm_lat = float(comm_row[LAT_COL]); comm_lon = float(comm_row[LON_COL])
comm_population = int(comm_row.get(comm_pop_col, 0) or 0)

# ---------- Helper to find nearest hospital matching a predicate ----------
def find_nearest(hosp_df, predicate=None):
    min_d = float('inf'); best_idx = None
    for h_idx, h in hosp_df.iterrows():
        try:
            hlat = float(h[LAT_COL]); hlon = float(h[LON_COL])
        except Exception:
            continue
        if predicate is not None and not predicate(h):
            continue
        try:
            d = geodesic((comm_lat, comm_lon), (hlat, hlon)).meters
        except Exception:
            continue
        if d < min_d:
            min_d = d; best_idx = h_idx
    if best_idx is None:
        return None, None
    return best_idx, min_d

# predicates for each rights type
def pred_uhc(row):
    if uhc_col:
        return truthy(row.get(uhc_col, ""))
    # fallback: try common text columns for keywords
    for c in hospitals.columns:
        if c.lower() in ('note','notes','type','remark','comment'):
            v = str(row.get(c, "") or "")
            if 'สิทธิบัตรทอง' in v or 'UHC' in v or 'gold' in v:
                return True
    return False

def pred_sss(row):
    if sss_col:
        return truthy(row.get(sss_col, ""))
    for c in hospitals.columns:
        if c.lower() in ('note','notes','type','remark','comment'):
            v = str(row.get(c, "") or "")
            if 'ประกันสังคม' in v or 'SSS' in v:
                return True
    return False

def pred_csmbs(row):
    if csmbs_col:
        return truthy(row.get(csmbs_col, ""))
    for c in hospitals.columns:
        if c.lower() in ('note','notes','type','remark','comment'):
            v = str(row.get(c, "") or "")
            if 'ข้าราชการ' in v or 'CSMBS' in v:
                return True
    return False

# ---------- Find nearest hospitals for each category ----------
nearest_any_idx, nearest_any_d = find_nearest(hospitals, predicate=None)
nearest_uhc_idx, nearest_uhc_d = find_nearest(hospitals, predicate=pred_uhc)
nearest_sss_idx, nearest_sss_d = find_nearest(hospitals, predicate=pred_sss)
nearest_csmbs_idx, nearest_csmbs_d = find_nearest(hospitals, predicate=pred_csmbs)

# ---------- Build map ----------
center = [comm_lat, comm_lon]
m = folium.Map(location=center, zoom_start=15, tiles=None)

# base tiles
folium.TileLayer(tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                 attr='&copy; <a href="https://carto.com/attributions">CARTO</a>',
                 name='แผนที่แบบหยาบ', control=True, show=True).add_to(m)
folium.TileLayer('OpenStreetMap', name='แผนที่แบบละเอียด', control=True, show=False).add_to(m)

# embed district containing community (if geojson available)
comm_district_label = None
if Path(DISTRICTS_SRC).exists():
    with open(DISTRICTS_SRC, 'r', encoding='utf-8') as f:
        districts_gj = json.load(f)
    district_features = districts_gj.get('features', []) or []
    # find containing feature
    containing = None
    for feat in district_features:
        geom = feat.get('geometry')
        if not geom:
            continue
        try:
            poly = shape(geom)
            if poly.contains(ShapelyPoint(comm_lon, comm_lat)):
                containing = feat
                break
        except Exception:
            continue
    if containing:
        props = containing.get('properties', {}) or {}
        label_field = next((k for k in ('amp_th','district','name','NAME') if k in props), None)
        label = props.get(label_field) if label_field else (props.get('name') or '—')
        props['district_name'] = label
        comm_district_label = label
        district_geo = {"type":"FeatureCollection","features":[{"type":"Feature","geometry":containing.get('geometry'), "properties":props}]}
        fg = FeatureGroup(name=f"District (highlight)", show=True, control=False).add_to(m)
        folium.GeoJson(data=district_geo,
                       style_function=lambda feat: {'fillColor':'#3388ff','color':'#000','weight':2.6,'fillOpacity':0.18,'interactive':True},
                       tooltip=GeoJsonTooltip(fields=['district_name'], aliases=['เขต:'], localize=True, sticky=True)
                       ).add_to(fg)

# fallback district label from community column if geojson not available or no containing feature
if not comm_district_label:
    comm_district_label = str(comm_row.get('เขต') or comm_row.get('district') or '').strip() or '—'

# ---------- Community marker ----------
comm_layer = FeatureGroup(name=f"Community: {TARGET_COMMUNITY_NAME}", show=True, control=False).add_to(m)
popup_comm = f"""
<div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:360px;">
  <div style="font-weight:700;font-size:16px;">{esc(TARGET_COMMUNITY_NAME)}</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.35;">
    <div><strong>เขต:</strong> {esc(comm_district_label)}</div>
    <div><strong>จำนวนประชากร:</strong> {comm_population}</div>
    <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
    <div><strong>โรงพยาบาลที่ใกล้ที่สุด:</strong> {esc(hospitals.loc[nearest_any_idx][hosp_name_col]) if nearest_any_idx is not None else 'N/A'} ({f'{nearest_any_d:.0f} m' if nearest_any_d is not None else 'N/A'})</div>
    <div><strong>โรงพยาบาลที่รับสิทธิบัตรทองใกล้ที่สุด:</strong> {esc(hospitals.loc[nearest_uhc_idx][hosp_name_col]) if nearest_uhc_idx is not None else 'N/A'} ({f'{nearest_uhc_d:.0f} m' if nearest_uhc_d is not None else 'N/A'})</div>
    <div><strong>โรงพยาบาลที่รับสิทธิประกันสังคมใกล้ที่สุด:</strong> {esc(hospitals.loc[nearest_sss_idx][hosp_name_col]) if nearest_sss_idx is not None else 'N/A'} ({f'{nearest_sss_d:.0f} m' if nearest_sss_d is not None else 'N/A'})</div>
    <div><strong>โรงพยาบาลที่รับสิทธิข้าราชการใกล้ที่สุด:</strong> {esc(hospitals.loc[nearest_csmbs_idx][hosp_name_col]) if nearest_csmbs_idx is not None else 'N/A'} ({f'{nearest_csmbs_d:.0f} m' if nearest_csmbs_d is not None else 'N/A'})</div>
  </div>
</div>
"""
folium.Marker(location=[comm_lat, comm_lon],
              icon=folium.CustomIcon(try_file_name(HOUSE_ICON_FN), ICON_SIZE, ICON_ANCHOR),
              popup=folium.Popup(popup_comm, max_width=420),
              tooltip=TARGET_COMMUNITY_NAME).add_to(comm_layer)

# ---------- Hospital layers / connections ----------
# Set control=False so these layers are NOT shown as toggles in LayerControl per user request
hosp_any_layer = FeatureGroup(name="Nearest hospital (any)", show=True, control=False).add_to(m)
hosp_uhc_layer = FeatureGroup(name="Nearest UHC hospital", show=True, control=False).add_to(m)
hosp_sss_layer = FeatureGroup(name="Nearest SSS hospital", show=True, control=False).add_to(m)
hosp_csmbs_layer = FeatureGroup(name="Nearest CSMBS hospital", show=True, control=False).add_to(m)
conn_layer = FeatureGroup(name="Connections", show=True, control=False).add_to(m)

ICON_URI = try_file_name(HOSP_ICON_FN)

def add_hospital_marker(layer, h_idx, color='#d32f2f', label_extra=""):
    h = hospitals.loc[h_idx]
    try:
        hlat = float(h[LAT_COL]); hlon = float(h[LON_COL])
    except Exception:
        return
    title = str(h.get(hosp_name_col) or "")
    district_val = h.get('เขต') or h.get('district') or ''
    tel_val = h.get('tel') or h.get('โทรศัพท์') or ''
    url_val = h.get('url') or h.get('website') or ''

    # rights status
    gold_v = rights_yesno(h, uhc_col, keywords=('สิทธิบัตรทอง','UHC','gold'))
    sss_v = rights_yesno(h, sss_col, keywords=('ประกันสังคม','SSS','social'))
    csmbs_v = rights_yesno(h, csmbs_col, keywords=('ข้าราชการ','CSMBS'))

    popup_html = f"""
    <div style="background:#EAF3FF;color:#1A1A1A;font-family:'Bai Jamjuree',sans-serif;padding:10px;border-radius:8px;border:2px solid #6C7A89;max-width:400px;">
      <div style="font-weight:700;font-size:15px;">{esc(title)} {label_extra}</div>
      <div style="margin-top:8px;font-size:14px;line-height:1.35;">
        <div><strong>เขต:</strong> {esc(district_val)}</div>
        <div><strong>เบอร์:</strong> {esc(tel_val)}</div>
        <div><strong>เว็บไซต์:</strong> <a href="{esc(url_val)}" target="_blank" rel="noopener noreferrer">{esc(url_val)}</a></div>
        <hr style="border:none;border-top:1px solid #d0d7dd;margin:8px 0;">
        <div><strong>สิทธิบัตรทอง:</strong> {gold_v}</div>
        <div><strong>สิทธิประกันสังคม:</strong> {sss_v}</div>
        <div><strong>สิทธิข้าราชการ:</strong> {csmbs_v}</div>
      </div>
    </div>
    """
    # try using icon, fallback to colored circle
    try:
        folium.Marker(location=[hlat, hlon],
                      icon=folium.CustomIcon(ICON_URI, ICON_SIZE, ICON_ANCHOR),
                      popup=folium.Popup(popup_html, max_width=420),
                      tooltip=title).add_to(layer)
    except Exception:
        folium.CircleMarker(location=[hlat, hlon], radius=6, color=color, fill=True, fill_color=color,
                            popup=folium.Popup(popup_html, max_width=420), tooltip=title).add_to(layer)
    # connection line to community
    try:
        folium.PolyLine(locations=[[comm_lat, comm_lon], [hlat, hlon]], color=color, weight=1.6, opacity=0.8).add_to(conn_layer)
    except Exception:
        pass

if nearest_any_idx is not None:
    add_hospital_marker(hosp_any_layer, nearest_any_idx, color='#d32f2f')
if nearest_uhc_idx is not None:
    add_hospital_marker(hosp_uhc_layer, nearest_uhc_idx, color='#ff9800', label_extra="(UHC)")
if nearest_sss_idx is not None:
    add_hospital_marker(hosp_sss_layer, nearest_sss_idx, color='#4caf50', label_extra="(SSS)")
if nearest_csmbs_idx is not None:
    add_hospital_marker(hosp_csmbs_layer, nearest_csmbs_idx, color='#6a1b9a', label_extra="(CSMBS)")

# ---------- CSS ----------
css = """
<link href="https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@400;600&display=swap" rel="stylesheet">
<style>
.leaflet-tooltip { font-family:'Bai Jamjuree',sans-serif !important; font-size:15px !important; color:#1A1A1A !important; background:#EAF3FF; border:2px solid #6C7A89; padding:6px; border-radius:8px; }
.leaflet-control-layers, .leaflet-control-layers .leaflet-control-layers-list, .leaflet-control-layers label { font-family:'Bai Jamjuree',sans-serif !important; font-size:14px !important; line-height:1.2 !important; }
</style>
"""
m.get_root().html.add_child(folium.Element(css))

# ---------- LayerControl and save ----------
# Layers exist on the map but were added with control=False for hospital/category/connection layers,
# so LayerControl will not show them as toggles (per user request).
folium.LayerControl(collapsed=False).add_to(m)
m.save(OUT_HTML)

print("Saved:", OUT_HTML)
print("Community:", TARGET_COMMUNITY_NAME)
print("Nearest hospitals found (any/UHC/SSS/CSMBS):",
      nearest_any_idx is not None, nearest_uhc_idx is not None, nearest_sss_idx is not None, nearest_csmbs_idx is not None)