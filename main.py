"""
Flask + simple API to serve districts GeoJSON and hospitals/communities as GeoJSON.

Place this file with:
- hospitals.csv
- communities.csv
- districts_bangkok.geojson
and folders:
- templates/index.html
- static/main.js

Run: python main.py
"""
import json
from pathlib import Path
from flask import Flask, render_template, jsonify, request, abort
import pandas as pd
from shapely.geometry import shape, Point

app = Flask(__name__, static_folder="static", template_folder="templates")

# Config: filenames (adjust as needed)
HOSPITALS_CSV = "hospitals.csv"
COMMUNITIES_CSV = "communities.csv"
GEOJSON_PATH = "districts_bangkok.geojson"

# Column names for lat/lon (adjust if your CSV uses other headers)
LAT_COL = 'ละติจูด'
LON_COL = 'ลองจิจูด'

def load_geojson():
    path = Path(GEOJSON_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{GEOJSON_PATH} not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_csv_as_geojson(csv_path, name_fields=None):
    """
    Load CSV into GeoJSON FeatureCollection of Points.
    name_fields: list of possible name columns; function picks first existing one.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"{csv_path} not found")
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    # find name col fallback to first column
    name_col = None
    if name_fields:
        for c in name_fields:
            if c in df.columns:
                name_col = c
                break
    if name_col is None:
        name_col = df.columns[0]
    # ensure numeric coords
    df[LAT_COL] = pd.to_numeric(df.get(LAT_COL), errors='coerce')
    df[LON_COL] = pd.to_numeric(df.get(LON_COL), errors='coerce')
    features = []
    for _, row in df.iterrows():
        if pd.isna(row[LAT_COL]) or pd.isna(row[LON_COL]):
            continue
        props = row.to_dict()
        # remove geometry columns from properties
        props.pop(LAT_COL, None)
        props.pop(LON_COL, None)
        # ensure strings for JSON
        for k,v in list(props.items()):
            try:
                json.dumps(v)
            except Exception:
                props[k] = str(v)
        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row[LON_COL]), float(row[LAT_COL])]},
            "properties": props
        }
        features.append(feat)
    return {"type": "FeatureCollection", "features": features}

# Cached loads to avoid re-reading on every request in dev
_geojson_cache = None
_hospitals_geojson_cache = None
_communities_geojson_cache = None

def get_districts_geojson():
    global _geojson_cache
    if _geojson_cache is None:
        _geojson_cache = load_geojson()
    return _geojson_cache

def get_hospitals_geojson():
    global _hospitals_geojson_cache
    if _hospitals_geojson_cache is None:
        _hospitals_geojson_cache = load_csv_as_geojson(HOSPITALS_CSV, name_fields=['โรงพยาบาล','ชื่อโรงพยาบาล','hospital','name','ชื่อ'])
    return _hospitals_geojson_cache

def get_communities_geojson():
    global _communities_geojson_cache
    if _communities_geojson_cache is None:
        _communities_geojson_cache = load_csv_as_geojson(COMMUNITIES_CSV, name_fields=['ชุมชน','ชื่อชุมชน','community','name','ชื่อ'])
    return _communities_geojson_cache

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/districts")
def api_districts():
    return jsonify(get_districts_geojson())

@app.route("/api/hospitals")
def api_hospitals():
    """
    Optional query params:
      - district=<district_name> : return hospitals within that district polygon
    """
    district_name = request.args.get("district")
    hospitals = get_hospitals_geojson()
    if not district_name:
        return jsonify(hospitals)
    # locate district polygon
    districts = get_districts_geojson()
    poly = None
    for feat in districts.get("features", []):
        if feat.get("properties", {}).get("amp_th") == district_name:
            poly = shape(feat.get("geometry"))
            break
    if poly is None:
        return abort(404, description="district not found")
    # filter hospitals
    filtered = {"type":"FeatureCollection","features":[]}
    for feat in hospitals.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        if coords:
            pt = Point(coords[0], coords[1])
            if poly.contains(pt):
                filtered["features"].append(feat)
    return jsonify(filtered)

@app.route("/api/communities")
def api_communities():
    district_name = request.args.get("district")
    communities = get_communities_geojson()
    if not district_name:
        return jsonify(communities)
    districts = get_districts_geojson()
    poly = None
    for feat in districts.get("features", []):
        if feat.get("properties", {}).get("amp_th") == district_name:
            poly = shape(feat.get("geometry"))
            break
    if poly is None:
        return abort(404, description="district not found")
    filtered = {"type":"FeatureCollection","features":[]}
    for feat in communities.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        if coords:
            pt = Point(coords[0], coords[1])
            if poly.contains(pt):
                filtered["features"].append(feat)
    return jsonify(filtered)

@app.route("/api/districts/<district_name>/stats")
def api_district_stats(district_name):
    districts = get_districts_geojson()
    target = None
    for feat in districts.get("features", []):
        if feat.get("properties", {}).get("amp_th") == district_name:
            target = feat
            break
    if target is None:
        return abort(404, description="district not found")
    poly = shape(target.get("geometry"))
    # count hospitals and communities
    hospitals = get_hospitals_geojson()
    communities = get_communities_geojson()
    hosp_in = []
    for feat in hospitals.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        if coords and poly.contains(Point(coords[0], coords[1])):
            hosp_in.append(feat.get("properties", {}))
    comm_in = []
    for feat in communities.get("features", []):
        coords = feat.get("geometry", {}).get("coordinates", [])
        if coords and poly.contains(Point(coords[0], coords[1])):
            comm_in.append(feat.get("properties", {}))
    # quick top hospitals sample (by available property or simply length)
    top_hospitals = []
    for p in hosp_in[:5]:
        top_hospitals.append({"name": p.get("โรงพยาบาล") or p.get("name") or p.get("ชื่อ") or list(p.values())[0]})
    return jsonify({
        "district": district_name,
        "num_hospitals": len(hosp_in),
        "num_communities": len(comm_in),
        "top_hospitals_sample": top_hospitals
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)