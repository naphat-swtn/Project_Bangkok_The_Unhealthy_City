import pandas as pd
import json

# โหลด dataset
hospitals = pd.read_csv("hospitals.csv")  
communities = pd.read_csv("communities.csv")

# export hospitals.json
hospitals_json = hospitals.to_dict(orient="records")
with open("hospitals.json", "w", encoding="utf-8") as f:
    json.dump(hospitals_json, f, ensure_ascii=False, indent=2)

# export communities.json
communities_json = communities.to_dict(orient="records")
with open("communities.json", "w", encoding="utf-8") as f:
    json.dump(communities_json, f, ensure_ascii=False, indent=2)

print("✅ Export เสร็จแล้ว: hospitals.json, communities.json")
