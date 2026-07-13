import streamlit as st
import requests
import pandas as pd

# Config
API_URL = "http://localhost:8000"

st.set_page_config(page_title="Trend Radar", layout="wide")
st.title("📡 Social Listening & Opportunity Radar")

# Sidebar - Controls
with st.sidebar:
    st.header("Controls")
    if st.button("Trigger Global Fetch"):
        requests.post(f"{API_URL}/jobs/run_all") # Trigger all scrapers
        st.success("Scrapers triggered!")

# Main Tabs
tab1, tab2, tab3 = st.tabs(["🔥 Top Pain Points", "📈 Emerging Trends", "💡 AI Solutions"])

with tab1:
    st.header("Recent Pain Points from Social Media")
    response = requests.get(f"{API_URL}/painpoints")
    if response.status_code == 200:
        data = response.json()
        if data:
            df = pd.DataFrame(data)
            # Display as interactive table or cards
            for _, item in df.iterrows():
                with st.expander(f"[{item['severity']}/10] {item['title'][:60]}..."):
                    st.write(f"**Audience:** {item['target_audience']}")
                    st.write(f"**Problem:** {item['description']}")
                    if st.button("Generate Solution", key=item['id']):
                        sol_res = requests.post(f"{API_URL}/solutions/generate", json={"pain_point_id": item['id']})
                        st.info("Generating solution via Claude...")
        else:
            st.info("No pain points detected yet. Wait for the scrapers to finish!")

with tab3:
    st.header("Saved AI Solutions")
    sol_response = requests.get(f"{API_URL}/solutions")
    if sol_response.status_code == 200:
        solutions = sol_response.json()
        for sol in solutions:
            st.subheader(sol['title'])
            st.markdown(sol['content'])
            st.divider()