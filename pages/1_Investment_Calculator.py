from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from btl_core import PROPERTY_TYPES, build_investment_projection, latest_snapshot


st.set_page_config(page_title="BTL Investment Calculator", page_icon="🧮", layout="wide")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
HPI = DATA / "hpi_history.csv"
RENTS = DATA / "rent_history.csv"
RATES = DATA / "boe_mortgage_rates.csv"

st.title("🧮 Buy-to-Let Investment Calculator")
st.caption(
    "Location-first deal modelling using the latest matched HMLR sale price and ONS rent for the selected property cohort. "
    "Every market value can be overridden for a specific property."
)
with st.sidebar:
    if st.button("← Back to Investment Dashboard", width="stretch"):
        st.switch_page("app.py")

if not HPI.exists() or not RENTS.exists():
    st.error("The official price and rent cache is missing. Run `python fetch_real_data.py` first.")
    st.stop()


@st.cache_data(show_spinner=False)
def load_market_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prices = pd.read_csv(HPI, parse_dates=["date"])
    rents = pd.read_csv(RENTS, parse_dates=["date"])
    rates = pd.read_csv(RATES, parse_dates=["date"]) if RATES.exists() else pd.DataFrame()
    return prices, rents, rates


prices, rents, rates = load_market_data()
available_types = [
    cohort for cohort in PROPERTY_TYPES
    if cohort in set(prices["property_type"]) and cohort in set(rents["property_type"])
]

st.subheader("1. Choose the market")
market_a, market_b = st.columns([1, 2])
with market_a:
    previous_type = st.session_state.get("selected_property_type", "Flat/maisonette")
    type_index = available_types.index(previous_type) if previous_type in available_types else 0
    property_type = st.selectbox("Property cohort", available_types, index=type_index)

latest_prices = latest_snapshot(prices, "avg_price", "property_type", property_type)
latest_rents = latest_snapshot(rents, "monthly_rent", "property_type", property_type)
market = latest_prices[["area_code", "area", "date", "avg_price"]].merge(
    latest_rents[["area_code", "date", "monthly_rent"]],
    on="area_code",
    how="inner",
    suffixes=("_price", "_rent"),
).sort_values("area")

if market.empty:
    st.error("No matched price and rent observations are available for this cohort.")
    st.stop()

area_names = dict(zip(market["area_code"], market["area"]))
area_codes = market["area_code"].tolist()
previous_code = st.session_state.get("selected_area_code")
area_index = area_codes.index(previous_code) if previous_code in area_codes else 0
with market_b:
    area_code = st.selectbox(
        "Local authority",
        area_codes,
        index=area_index,
        format_func=lambda code: area_names.get(code, code),
    )

market_row = market[market["area_code"] == area_code].iloc[0]
st.session_state["selected_area_code"] = str(area_code)
st.session_state["selected_area"] = str(market_row["area"])
st.session_state["selected_property_type"] = property_type

st.info(
    f"Latest matched starting point for **{market_row['area']} · {property_type}**: "
    f"**£{market_row['avg_price']:,.0f}** average value ({market_row['date_price']:%b %Y}) and "
    f"**£{market_row['monthly_rent']:,.0f} pcm** rent ({market_row['date_rent']:%b %Y})."
)

deal_key = f"{area_code}_{property_type.lower().replace('/', '_').replace('-', '_').replace(' ', '_')}"
latest_rate = float(rates.iloc[-1]["mortgage_rate_75ltv_2y"]) if not rates.empty else 4.75

st.subheader("2. Enter the deal and assumptions")
acquisition, finance, income = st.columns(3)

with acquisition:
    st.markdown("**Purchase and exit**")
    purchase_price = st.number_input(
        "Property purchase price (£)", min_value=10_000.0, max_value=10_000_000.0,
        value=float(round(market_row["avg_price"], -3)), step=5_000.0, key=f"price_{deal_key}",
    )
    deposit = st.number_input(
        "Deposit / cash put down (£)", min_value=0.0, max_value=float(purchase_price),
        value=float(round(purchase_price * 0.25, -3)), step=5_000.0, key=f"deposit_{deal_key}",
    )
    purchase_costs = st.number_input(
        "Purchase costs incl. tax and fees (£)", min_value=0.0,
        value=float(round(purchase_price * 0.05, -2)), step=1_000.0, key=f"purchase_costs_{deal_key}",
    )
    selling_cost_pct = st.number_input(
        "Selling costs (% of future value)", min_value=0.0, max_value=10.0,
        value=1.5, step=0.1, key=f"selling_costs_{deal_key}",
    )

with finance:
    st.markdown("**Mortgage**")
    repayment_type = st.radio(
        "Mortgage type", ["Repayment", "Interest-only"], horizontal=True, key=f"repayment_{deal_key}"
    )
    mortgage_rate = st.number_input(
        "Mortgage interest rate (%)", min_value=0.0, max_value=20.0,
        value=float(round(latest_rate, 2)), step=0.1, key=f"rate_{deal_key}",
    )
    mortgage_term = st.number_input(
        "Mortgage term (years)", min_value=1, max_value=40, value=25, step=1, key=f"term_{deal_key}"
    )
    holding_years = st.slider(
        "Investment holding period (years)", min_value=1, max_value=int(mortgage_term),
        value=min(5, int(mortgage_term)), key=f"hold_{deal_key}_{mortgage_term}",
    )
    st.metric("Amount financed", f"£{purchase_price - deposit:,.0f}")
    st.caption(f"Loan-to-value: {(purchase_price - deposit) / purchase_price * 100:.1f}%")

with income:
    st.markdown("**Rent, growth and running costs**")
    monthly_rent = st.number_input(
        "Monthly rent (£)", min_value=0.0, max_value=100_000.0,
        value=float(round(market_row["monthly_rent"])), step=50.0, key=f"rent_{deal_key}",
    )
    property_growth = st.number_input(
        "Annual property value growth (%)", min_value=-20.0, max_value=30.0,
        value=3.0, step=0.25, key=f"growth_{deal_key}",
    )
    rent_growth = st.number_input(
        "Annual rent growth (%)", min_value=-20.0, max_value=30.0,
        value=2.5, step=0.25, key=f"rent_growth_{deal_key}",
    )
    void_pct = st.number_input(
        "Void / non-collection (%)", min_value=0.0, max_value=50.0,
        value=5.0, step=0.5, key=f"void_{deal_key}",
    )
    operating_cost_pct = st.number_input(
        "Variable operating costs (% of gross rent)", min_value=0.0, max_value=100.0,
        value=12.0, step=0.5, key=f"operating_{deal_key}",
    )
    fixed_annual_costs = st.number_input(
        "Fixed annual costs (£)", min_value=0.0, value=2_000.0,
        step=250.0, key=f"fixed_costs_{deal_key}",
    )

try:
    results, projection = build_investment_projection(
        purchase_price=purchase_price,
        deposit=deposit,
        monthly_rent=monthly_rent,
        holding_years=int(holding_years),
        property_growth_pct=property_growth,
        rent_growth_pct=rent_growth,
        mortgage_rate_pct=mortgage_rate,
        mortgage_term_years=int(mortgage_term),
        repayment_type=repayment_type,
        void_pct=void_pct,
        operating_cost_pct=operating_cost_pct,
        fixed_annual_costs=fixed_annual_costs,
        purchase_costs=purchase_costs,
        selling_cost_pct=selling_cost_pct,
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

st.subheader("3. Returns")
headline = st.columns(6)
headline[0].metric("Gross yield", f"{results['gross_yield']:.2f}%")
headline[1].metric("Net operating yield", f"{results['net_operating_yield']:.2f}%")
headline[2].metric("Cash-on-cash · Y1", f"{results['cash_on_cash']:.2f}%")
headline[3].metric("Economic ROE · Y1", "n/a" if np.isnan(results["year_1_roe"]) else f"{results['year_1_roe']:.2f}%")
headline[4].metric(f"Total ROI · {holding_years}Y", f"{results['total_roi']:.2f}%")
headline[5].metric("Annualised ROI", "n/a" if np.isnan(results["annualised_roi"]) else f"{results['annualised_roi']:.2f}%")

detail = st.columns(6)
detail[0].metric("Initial cash invested", f"£{results['initial_cash_invested']:,.0f}")
detail[1].metric("Monthly mortgage", f"£{results['monthly_mortgage_payment']:,.0f}")
detail[2].metric("Future property value", f"£{results['future_property_value']:,.0f}")
detail[3].metric("Remaining mortgage", f"£{results['remaining_mortgage']:,.0f}")
detail[4].metric("Cumulative cash flow", f"£{results['cumulative_cashflow']:,.0f}")
detail[5].metric("Total projected profit", f"£{results['total_profit']:,.0f}")

chart_left, chart_right = st.columns(2)
with chart_left:
    balance_chart = px.line(
        projection, x="year", y=["property_value", "equity", "mortgage_balance"], markers=True,
        title="Property value, equity and mortgage balance",
        labels={"value": "£", "year": "Year", "variable": "Series"},
    )
    st.plotly_chart(balance_chart, width="stretch")
with chart_right:
    cashflow_chart = px.bar(
        projection, x="year", y="annual_cashflow", title="Annual cash flow after mortgage payments",
        labels={"annual_cashflow": "Cash flow (£)", "year": "Year"},
    )
    cashflow_chart.add_hline(y=0, line_dash="dash")
    st.plotly_chart(cashflow_chart, width="stretch")

with st.expander("Year-by-year projection"):
    table = projection.rename(columns={
        "year": "Year", "property_value": "Property value", "gross_rent": "Gross rent",
        "noi": "Net operating income", "mortgage_payments": "Mortgage payments",
        "interest_paid": "Interest paid", "principal_repaid": "Principal repaid",
        "annual_cashflow": "Cash flow", "mortgage_balance": "Mortgage balance",
        "equity": "Equity", "appreciation": "Appreciation", "roe": "Economic ROE (%)",
    })
    st.dataframe(table, hide_index=True, width="stretch")

with st.expander("How the metrics are calculated"):
    st.markdown(
        """
- **Gross yield** = first-year gross rent ÷ purchase price.
- **Net operating yield** = rent after voids and operating costs, before finance, ÷ purchase price.
- **Cash-on-cash return** = first-year cash flow after mortgage payments ÷ deposit and purchase costs.
- **Economic ROE** = NOI less mortgage interest plus property appreciation ÷ equity at the start of the year. Principal repayment builds equity but is not treated as profit.
- **Total ROI** = cumulative cash flow plus net sale proceeds, less initial cash invested, ÷ initial cash invested.
- **Annualised ROI** is the annual internal rate of return on the initial investment, annual cash flows, and net exit proceeds.
        """
    )

st.caption(
    "Projection only: growth, rent, costs and rates are assumptions rather than forecasts. Purchase costs must include the stamp duty and fees applicable to you. Taxation is excluded. This is not financial advice."
)
