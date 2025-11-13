import pandas as pd
from geopy.distance import geodesic
import numpy as np

# โหลดข้อมูล
hospitals = pd.read_csv("hospitals.csv")
communities = pd.read_csv("communities.csv")

# ตรวจสอบชื่อคอลัมน์
print("Hospitals columns:", hospitals.columns)
print("Communities columns:", communities.columns)

# ฟังก์ชันหาค่าโรงพยาบาลใกล้ที่สุด
def find_nearest_hospital(lat, lon, hospitals_df):
    community_coord = (lat, lon)

    # คำนวณระยะทางทั้งหมด
    hospitals_df["distance_m"] = hospitals_df.apply(
        lambda row: geodesic(
            (row["ละติจูด"], row["ลองจิจูด"]),
            community_coord
        ).meters,
        axis=1
    )

    # เลือกโรงพยาบาลที่ใกล้ที่สุด
    nearest = hospitals_df.loc[hospitals_df["distance_m"].idxmin()]
    return nearest["โรงพยาบาล"], nearest["distance_m"]

# เพิ่มคอลัมน์ใหม่ใน communities
communities["โรงพยาบาลใกล้ที่สุด"] = np.nan
communities["ระยะทาง(เมตร)"] = np.nan

for i, row in communities.iterrows():
    nearest_name, nearest_dist = find_nearest_hospital(row["ละติจูด"], row["ลองจิจูด"], hospitals)
    communities.at[i, "โรงพยาบาลใกล้ที่สุด"] = nearest_name
    communities.at[i, "ระยะทาง(เมตร)"] = nearest_dist

# บันทึกผลลัพธ์
communities.to_csv("communities_with_nearest_hospital.csv", index=False, encoding="utf-8-sig")

print("✅ เสร็จแล้ว! ได้ไฟล์ communities_with_nearest_hospital.csv")
