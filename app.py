import streamlit as st
import pandas as pd
import pydeck as pdk

# โหลดข้อมูล
hospitals = pd.read_csv("hospitals.csv")
communities = pd.read_csv("communities.csv")

st.set_page_config(layout="wide")

# ----------- Custom CSS for floating panel -----------
st.markdown("""
    <style>
    .floating-panel {
        position: absolute;
        top: 50px;
        left: 20px;
        background: white;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 9999;
        width: 260px;
        font-family: 'Segoe UI', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# ----------- Floating Panel UI -----------
with st.container():
    st.markdown('<div class="floating-panel">', unsafe_allow_html=True)

    st.subheader("🔍 Search / Filter")
    search = st.text_input("ค้นหาชุมชน/โรงพยาบาล")
    filter_rights = st.multiselect("สิทธิ", ["สิทธิบัตรทอง", "สิทธิประกันสังคม", "สิทธิข้าราชการ"], default=[])

    st.markdown("**🏘 Top 10 Communities (by distance)**")
    top10 = communities.head(10)  # <-- คุณสามารถเปลี่ยน logic การ sort ได้
    selected_comm = st.selectbox("เลือกชุมชน", top10["ชุมชน"].tolist())

    st.markdown('</div>', unsafe_allow_html=True)

# ----------- Base Map -----------
initial_view = pdk.ViewState(
    latitude=communities["ละติจูด"].mean(),
    longitude=communities["ลองจิจูด"].mean(),
    zoom=11,
    pitch=0,
)

# Marker: Hospitals
hospital_layer = pdk.Layer(
    "ScatterplotLayer",
    data=hospitals,
    get_position=["ลองจิจูด", "ละติจูด"],
    get_color=[255, 0, 0, 180],
    get_radius=100,
    radius_units="meters",
    pickable=True,
    radius_scale=5,
    radius_min_pixels=4,
    radius_max_pixels=20,  # <<< ป้องกันไม่ให้ใหญ่เกินไปตอน zoom out
)

# Marker: Communities
community_layer = pdk.Layer(
    "ScatterplotLayer",
    data=communities,
    get_position=["ลองจิจูด", "ละติจูด"],
    get_color=[0, 0, 255, 180],
    get_radius=80,
    radius_units="meters",
    pickable=True,
    radius_scale=5,
    radius_min_pixels=3,
    radius_max_pixels=15,
)

# ----------- Render Map -----------
r = pdk.Deck(
    map_style="mapbox://styles/mapbox/light-v9",
    initial_view_state=initial_view,
    layers=[hospital_layer, community_layer],
    tooltip={"text": "{ชุมชน} / {โรงพยาบาล}"}
)

st.pydeck_chart(r, use_container_width=True)