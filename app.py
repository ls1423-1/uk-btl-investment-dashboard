from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from btl_core import PROPERTY_TYPES, area_history, build_market_snapshot, load_geojson, validate_bedroom_sales

st.set_page_config(page_title="UK Buy-to-Let Intelligence", page_icon="🏘️", layout="wide")
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HPI = DATA / "hpi_history.csv"
RENTS = DATA / "rent_history.csv"
RATES = DATA / "boe_mortgage_rates.csv"
GEO = DATA / "lad_boundaries.geojson"
BEDS_RENT_HISTORY = DATA / "rent_bedroom_history.csv"
LEGACY_BEDS_RENT = DATA / "rent_bedroom_current.csv"
BEDS_RENT = BEDS_RENT_HISTORY if BEDS_RENT_HISTORY.exists() else LEGACY_BEDS_RENT
BEDS_SALES = DATA / "bedroom_sales_history.csv"

st.title("🏘️ UK Buy-to-Let Intelligence")
st.markdown("**v0.3.3 · LIVE ONS HISTORY + MAP + MATCHED COHORTS**")
st.caption("Map-first screening using HMLR/UK HPI, ONS PIPR and Bank of England data. Like-for-like property-type cohorts; no demo values.")

required = [HPI, RENTS, GEO]
if any(not p.exists() for p in required):
    st.error("Official data cache is incomplete. Refresh it before using the dashboard.")
    st.code("python fetch_real_data.py\npython -m streamlit run app.py", language="bash")
    st.stop()

@st.cache_data(show_spinner="Loading market data…")
def load_app_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    price_data = pd.read_csv(HPI, parse_dates=["date"])
    rent_data = pd.read_csv(RENTS, parse_dates=["date"])
    rate_data = pd.read_csv(RATES, parse_dates=["date"]) if RATES.exists() else pd.DataFrame()
    boundaries = load_geojson(GEO)
    return price_data, rent_data, rate_data, boundaries


prices, rents, rates, geojson = load_app_data()
latest_rate = float(rates.iloc[-1].mortgage_rate_75ltv_2y) if not rates.empty else 4.75

with st.sidebar:
    if st.button("🧮 Open Investment Calculator", type="primary", width="stretch"):
        st.switch_page("pages/1_Investment_Calculator.py")
    st.divider()
    st.header("Comparable property")
    available_types = [x for x in PROPERTY_TYPES if x in set(prices.property_type) and x in set(rents.property_type)]
    if not available_types:
        st.error("No matched property-type cohorts were found in the refreshed data.")
        st.stop()
    default_idx = available_types.index("Flat/maisonette") if "Flat/maisonette" in available_types else 0
    property_type = st.selectbox("Property type", available_types, index=default_idx, key="dashboard_property_type")
    st.caption("Yield uses rent and sale price from the same selected property type.")
    st.divider()
    st.header("Financing & costs")
    ltv = st.slider("Loan-to-value (%)", 0, 85, 75, 5)
    mortgage_rate = st.slider("Mortgage rate (%)", 1.0, 10.0, float(round(latest_rate, 2)), 0.05)
    running_cost_pct = st.slider("Annual running costs (% of value)", 0.0, 5.0, 1.25, 0.05)
    void_pct = st.slider("Void / non-collection (%)", 0.0, 20.0, 5.0, 1.0)
    st.divider()
    st.header("Screen")
    max_price = st.number_input("Max average price (£)", 50_000, 3_000_000, 600_000, 25_000)
    min_yield = st.slider("Min gross yield (%)", 0.0, 15.0, 0.0, 0.25)
    min_cashflow = st.number_input("Min monthly cash flow (£)", -5000, 5000, -5000, 50)

snapshot = build_market_snapshot(prices, rents, property_type, ltv, mortgage_rate, running_cost_pct, void_pct)
snapshot = snapshot[(snapshot.avg_price <= max_price) & (snapshot.gross_yield >= min_yield) & (snapshot.monthly_cashflow >= min_cashflow)].copy()
if snapshot.empty:
    st.warning("No areas meet the current cohort and filters.")
    st.stop()

# Detect the ONS boundary code property without hard-coding a vintage-specific name.
feature_props = geojson.get("features", [{}])[0].get("properties", {}) if geojson.get("features") else {}
code_prop = next((k for k in feature_props if k.upper().endswith("CD") and "LAD" in k.upper()), None)
name_prop = next((k for k in feature_props if k.upper().endswith("NM") and "LAD" in k.upper()), None)
if code_prop is None:
    code_prop = next((k for k in feature_props if "CD" in k.upper()), None)

metric_options = {
    "Investment score": "investment_score",
    "Gross yield %": "gross_yield",
    "Net yield %": "net_yield",
    "Price growth 1Y %": "price_growth_1y",
    "Price growth 3Y %": "price_growth_3y",
    "Price growth 5Y %": "price_growth_5y",
    "Rent growth 1Y %": "rent_growth_1y",
    "Rent growth 3Y %": "rent_growth_3y",
    "Monthly cash flow £": "monthly_cashflow",
    "Break-even mortgage rate %": "break_even_rate",
}

m1, m2, m3, m4, m5 = st.columns(5)
best = snapshot.sort_values("investment_score", ascending=False).iloc[0]
m1.metric("Areas", len(snapshot))
m2.metric("Top area", best.area)
m3.metric("Score", f"{best.investment_score:.0f}/100")
m4.metric("Gross yield", f"{best.gross_yield:.2f}%")
m5.metric("3Y price growth", "n/a" if pd.isna(best.price_growth_3y) else f"{best.price_growth_3y:.1f}%")

st.subheader("Investment map")
map_left, map_right = st.columns([3.1, 1.2])
with map_left:
    layer_name = st.selectbox("Colour map by", list(metric_options), index=0, label_visibility="collapsed")
    layer = metric_options[layer_name]
    if code_prop:
        map_function = getattr(px, "choropleth_map", None)
        map_arguments = {
            "geojson": geojson,
            "locations": "area_code",
            "featureidkey": f"properties.{code_prop}",
            "color": layer,
            "hover_name": "area",
            "hover_data": {"avg_price": ":,.0f", "monthly_rent": ":,.0f", "gross_yield": ":.2f", "price_growth_3y": ":.1f", "monthly_cashflow": ":,.0f", "area_code": False},
            "center": {"lat": 54.6, "lon": -3.0},
            "zoom": 4.35,
            "opacity": 0.72,
            "labels": {layer: layer_name},
            "height": 650,
        }
        if map_function is not None:
            map_arguments["map_style"] = "carto-positron"
        else:
            map_function = px.choropleth_mapbox
            map_arguments["mapbox_style"] = "carto-positron"
        fig = map_function(snapshot, **map_arguments)
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.error("Could not identify the local-authority code field in the downloaded boundary file.")
with map_right:
    st.markdown("**Map cohort**")
    st.write(property_type)
    st.markdown("**What the colour means**")
    st.write(layer_name)
    st.markdown("**Data integrity**")
    st.write("Property-type yield is matched like-for-like. Bedroom-specific yield is not shown unless a bedroom-level sales enrichment is installed.")

st.subheader("Inspect an area")
ranked = snapshot.sort_values("investment_score", ascending=False)
area_options = ranked.area.tolist()
previous_area = st.session_state.get("selected_area")
area_index = area_options.index(previous_area) if previous_area in area_options else 0
chosen = st.selectbox("Area", area_options, index=area_index, key="dashboard_area")
row = ranked[ranked.area == chosen].iloc[0]
st.session_state["selected_area"] = str(row.area)
st.session_state["selected_area_code"] = str(row.area_code)
st.session_state["selected_property_type"] = property_type

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Average price", f"£{row.avg_price:,.0f}")
c2.metric("Monthly rent", f"£{row.monthly_rent:,.0f}")
c3.metric("Gross yield", f"{row.gross_yield:.2f}%")
c4.metric("Net yield", f"{row.net_yield:.2f}%")
c5.metric("Monthly cash flow", f"£{row.monthly_cashflow:,.0f}")
c6.metric("Break-even rate", f"{row.break_even_rate:.2f}%")

g1, g2, g3, g4 = st.columns(4)
g1.metric("Price growth 1Y", "n/a" if pd.isna(row.price_growth_1y) else f"{row.price_growth_1y:.1f}%")
g2.metric("Price growth 3Y", "n/a" if pd.isna(row.price_growth_3y) else f"{row.price_growth_3y:.1f}%")
g3.metric("Price growth 5Y", "n/a" if pd.isna(row.price_growth_5y) else f"{row.price_growth_5y:.1f}%")
g4.metric("Rent growth 1Y", "n/a" if pd.isna(row.rent_growth_1y) else f"{row.rent_growth_1y:.1f}%")

hist = area_history(prices, rents, row.area_code, property_type, ltv, rates, running_cost_pct, void_pct)
if not hist.empty:
    chart1 = go.Figure()
    chart1.add_trace(go.Scatter(x=hist.date, y=hist.avg_price, name="Average sale price", yaxis="y1"))
    chart1.update_layout(title=f"{chosen} · {property_type} sale-price history", yaxis_title="Average sale price (£)", height=360, margin=dict(t=55,b=20))
    st.plotly_chart(chart1, width="stretch")

    a, b = st.columns(2)
    with a:
        rent_fig = px.line(hist.dropna(subset=["monthly_rent"]), x="date", y="monthly_rent", title="Monthly rent history", labels={"monthly_rent":"Monthly rent (£)","date":""})
        st.plotly_chart(rent_fig, width="stretch")
    with b:
        yield_fig = px.line(hist.dropna(subset=["gross_yield"]), x="date", y="gross_yield", title="Gross yield through time", labels={"gross_yield":"Gross yield (%)","date":""})
        st.plotly_chart(yield_fig, width="stretch")

    if "monthly_cashflow" in hist and hist.monthly_cashflow.notna().any():
        cf_fig = px.line(hist, x="date", y="monthly_cashflow", title="Historical financing-aware cash flow", labels={"monthly_cashflow":"Monthly cash flow (£)","date":""})
        cf_fig.add_hline(y=0, line_dash="dash")
        st.plotly_chart(cf_fig, width="stretch")

st.subheader("Rank and compare")
display_cols = ["area","avg_price","monthly_rent","gross_yield","net_yield","price_growth_1y","price_growth_3y","price_growth_5y","rent_growth_1y","monthly_cashflow","cash_on_cash","icr","investment_score"]
st.dataframe(ranked[display_cols], hide_index=True, width="stretch", height=430, column_config={
    "avg_price": st.column_config.NumberColumn("Avg price", format="£%.0f"),
    "monthly_rent": st.column_config.NumberColumn("Monthly rent", format="£%.0f"),
    "monthly_cashflow": st.column_config.NumberColumn("Monthly cash flow", format="£%.0f"),
})

compare = st.multiselect("Compare areas", ranked.area.tolist(), default=ranked.area.head(min(3, len(ranked))).tolist(), max_selections=5)
if compare:
    comp = ranked[ranked.area.isin(compare)].copy()
    compfig = px.scatter(comp, x="price_growth_3y", y="gross_yield", size="avg_price", hover_name="area", text="area", labels={"price_growth_3y":"3Y price growth (%)","gross_yield":"Gross yield (%)"})
    compfig.update_traces(textposition="top center")
    st.plotly_chart(compfig, width="stretch")

st.subheader("Mortgage-rate stress")
stress = pd.DataFrame({"Mortgage rate": np.arange(2.0, 9.05, 0.25)})
stress["Monthly cash flow"] = (row.net_operating_income - row.loan * stress["Mortgage rate"] / 100) / 12
sf = px.line(stress, x="Mortgage rate", y="Monthly cash flow", markers=True, title=f"{chosen}: interest-only cash flow sensitivity")
sf.add_hline(y=0, line_dash="dash")
st.plotly_chart(sf, width="stretch")

st.subheader("Bedroom cohorts")
valid_beds, bed_message = validate_bedroom_sales(BEDS_SALES)
if BEDS_RENT.exists():
    br = pd.read_csv(BEDS_RENT, parse_dates=["date"])
    area_beds = br[br.area_code == row.area_code].sort_values("date").groupby("bedrooms", as_index=False).tail(1)
    if not area_beds.empty:
        bf = px.bar(area_beds, x="bedrooms", y="monthly_rent", title=f"{chosen}: latest ONS rent by bedroom count", labels={"bedrooms":"Bedrooms","monthly_rent":"Monthly rent (£)"})
        st.plotly_chart(bf, width="stretch")
if valid_beds and BEDS_RENT.exists():
    bs = pd.read_csv(BEDS_SALES, parse_dates=["date"])
    br = pd.read_csv(BEDS_RENT, parse_dates=["date"])
    selected_bed = st.selectbox("Bedroom cohort", sorted(set(bs.bedrooms.astype(str)) & set(br.bedrooms.astype(str))))
    bs["bedrooms"] = bs.bedrooms.astype(str)
    br["bedrooms"] = br.bedrooms.astype(str)
    br = br.sort_values("date").groupby(["area_code", "bedrooms"], as_index=False).tail(1)
    bss = bs[bs.bedrooms == selected_bed].sort_values("date")
    latest_bs = bss.groupby("area_code", as_index=False).tail(1)
    bg = []
    for code, gg in bss.groupby("area_code"):
        gg = gg.sort_values("date")
        cur = gg.avg_price.iloc[-1] if len(gg) else np.nan
        def gr(n):
            if len(gg) <= n or gg.avg_price.iloc[-1-n] == 0: return np.nan
            return 100 * (cur / gg.avg_price.iloc[-1-n] - 1)
        bg.append({"area_code":code,"bed_price_growth_1y":gr(12),"bed_price_growth_3y":gr(36),"bed_price_growth_5y":gr(60)})
    bed_snap = latest_bs.merge(pd.DataFrame(bg), on="area_code", how="left").merge(br[br.bedrooms == selected_bed][["area_code","monthly_rent"]], on="area_code", how="inner")
    bed_snap["gross_yield"] = 100 * bed_snap.monthly_rent * 12 / bed_snap.avg_price
    if not bed_snap.empty:
        st.success(bed_message + " Exact bedroom-matched current yield and sale-price growth are active.")
        st.dataframe(bed_snap[["area","avg_price","monthly_rent","gross_yield","bed_price_growth_1y","bed_price_growth_3y","bed_price_growth_5y"]].sort_values("gross_yield", ascending=False), hide_index=True, width="stretch")
else:
    st.warning("Exact 1-bed vs 1-bed / 2-bed vs 2-bed sale-price growth and yield is not fabricated here. ONS supplies bedroom rents, but HMLR Price Paid Data does not contain bedroom count. Add data/bedroom_sales_history.csv from a genuine EPC/listings enrichment to activate exact matching.")
    st.caption("Required columns: date, area_code, area, bedrooms, avg_price. Optional: transactions, property_type, source.")

if not rates.empty:
    st.subheader("Mortgage rates over time")
    rf = px.line(rates, x="date", y="mortgage_rate_75ltv_2y", labels={"date":"","mortgage_rate_75ltv_2y":"Rate (%)"}, title="Bank of England quoted 2-year fixed mortgage rate at 75% LTV")
    st.plotly_chart(rf, width="stretch")

st.caption("Sources: HM Land Registry UK House Price Index and Price Paid Data; ONS Price Index of Private Rents; Bank of England; ONS Open Geography boundaries. Latest HPI observations can be provisional and later revised. This is a screening/research tool, not investment advice.")
