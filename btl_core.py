from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

PROPERTY_TYPES = ["Overall", "Detached", "Semi-detached", "Terraced", "Flat/maisonette"]
BEDROOMS = ["1", "2", "3", "4+"]


def monthly_mortgage_payment(
    principal: float,
    annual_rate_pct: float,
    term_years: int,
    repayment_type: str = "Repayment",
) -> float:
    """Return the contractual monthly mortgage payment.

    ``Repayment`` amortises principal over the term. ``Interest-only`` pays only
    monthly interest and leaves the original principal outstanding.
    """
    if principal < 0 or annual_rate_pct < 0 or term_years <= 0:
        raise ValueError("Principal/rate must be non-negative and term must be positive.")
    if principal == 0:
        return 0.0
    monthly_rate = annual_rate_pct / 1200.0
    if repayment_type.strip().lower() == "interest-only":
        return principal * monthly_rate
    if repayment_type.strip().lower() != "repayment":
        raise ValueError("Repayment type must be 'Repayment' or 'Interest-only'.")
    months = int(term_years * 12)
    if monthly_rate == 0:
        return principal / months
    factor = (1.0 + monthly_rate) ** months
    return principal * monthly_rate * factor / (factor - 1.0)


def mortgage_amortisation(
    principal: float,
    annual_rate_pct: float,
    term_years: int,
    months: int,
    repayment_type: str = "Repayment",
) -> pd.DataFrame:
    """Build a month-by-month interest, principal and balance schedule."""
    if months < 0:
        raise ValueError("Months cannot be negative.")
    payment = monthly_mortgage_payment(principal, annual_rate_pct, term_years, repayment_type)
    monthly_rate = annual_rate_pct / 1200.0
    balance = float(principal)
    rows = []
    for month in range(1, months + 1):
        interest = balance * monthly_rate
        if repayment_type.strip().lower() == "interest-only":
            principal_paid = 0.0
            actual_payment = interest
        elif balance > 0:
            principal_paid = min(max(payment - interest, 0.0), balance)
            actual_payment = interest + principal_paid
        else:
            principal_paid = actual_payment = interest = 0.0
        balance = max(balance - principal_paid, 0.0)
        rows.append({
            "month": month,
            "payment": actual_payment,
            "interest": interest,
            "principal_paid": principal_paid,
            "balance": balance,
        })
    return pd.DataFrame(rows)


def _annual_irr(cashflows: list[float]) -> float:
    """Calculate a conventional annual IRR without an extra dependency."""
    if len(cashflows) < 2 or cashflows[0] >= 0 or not any(x > 0 for x in cashflows[1:]):
        return np.nan

    def npv(rate: float) -> float:
        return sum(value / ((1.0 + rate) ** year) for year, value in enumerate(cashflows))

    low, high = -0.9999, 1.0
    low_value, high_value = npv(low), npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2.0
        high_value = npv(high)
    if low_value * high_value > 0:
        return np.nan
    for _ in range(120):
        mid = (low + high) / 2.0
        mid_value = npv(mid)
        if abs(mid_value) < 1e-8:
            return mid
        if low_value * mid_value <= 0:
            high = mid
        else:
            low, low_value = mid, mid_value
    return (low + high) / 2.0


def build_investment_projection(
    purchase_price: float,
    deposit: float,
    monthly_rent: float,
    holding_years: int,
    property_growth_pct: float,
    rent_growth_pct: float,
    mortgage_rate_pct: float,
    mortgage_term_years: int,
    repayment_type: str,
    void_pct: float,
    operating_cost_pct: float,
    fixed_annual_costs: float,
    purchase_costs: float,
    selling_cost_pct: float,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Project property equity and investor returns over a selected hold.

    ROI includes all projected after-debt cash flows and net sale proceeds. ROE
    measures annual economic return (NOI less interest plus appreciation) against
    equity at the start of each year; principal repayment is not counted as profit.
    """
    if purchase_price <= 0 or not (0 <= deposit <= purchase_price):
        raise ValueError("Deposit must be between zero and the purchase price.")
    if monthly_rent < 0 or holding_years <= 0 or mortgage_term_years <= 0:
        raise ValueError("Rent cannot be negative and time periods must be positive.")
    if holding_years > mortgage_term_years and deposit < purchase_price:
        raise ValueError("Holding period cannot exceed the mortgage term in this model.")
    if any(x < 0 for x in (mortgage_rate_pct, void_pct, operating_cost_pct, fixed_annual_costs, purchase_costs, selling_cost_pct)):
        raise ValueError("Rates and costs cannot be negative.")

    mortgage = purchase_price - deposit
    initial_cash = deposit + purchase_costs
    schedule = mortgage_amortisation(
        mortgage, mortgage_rate_pct, mortgage_term_years, holding_years * 12, repayment_type
    )
    rows = []
    for year in range(1, holding_years + 1):
        year_schedule = schedule.iloc[(year - 1) * 12:year * 12]
        start_balance = mortgage if year == 1 else float(schedule.iloc[(year - 1) * 12 - 1]["balance"])
        end_balance = float(year_schedule.iloc[-1]["balance"]) if not year_schedule.empty else start_balance
        start_value = purchase_price * (1.0 + property_growth_pct / 100.0) ** (year - 1)
        end_value = purchase_price * (1.0 + property_growth_pct / 100.0) ** year
        gross_rent = monthly_rent * 12.0 * (1.0 + rent_growth_pct / 100.0) ** (year - 1)
        void_loss = gross_rent * void_pct / 100.0
        operating_costs = gross_rent * operating_cost_pct / 100.0 + fixed_annual_costs
        noi = gross_rent - void_loss - operating_costs
        mortgage_payments = float(year_schedule["payment"].sum())
        interest = float(year_schedule["interest"].sum())
        principal_paid = float(year_schedule["principal_paid"].sum())
        cashflow = noi - mortgage_payments
        start_equity = start_value - start_balance
        appreciation = end_value - start_value
        economic_return = noi - interest + appreciation
        rows.append({
            "year": year,
            "property_value": end_value,
            "gross_rent": gross_rent,
            "noi": noi,
            "mortgage_payments": mortgage_payments,
            "interest_paid": interest,
            "principal_repaid": principal_paid,
            "annual_cashflow": cashflow,
            "mortgage_balance": end_balance,
            "equity": end_value - end_balance,
            "appreciation": appreciation,
            "roe": 100.0 * economic_return / start_equity if start_equity > 0 else np.nan,
        })

    projection = pd.DataFrame(rows)
    first = projection.iloc[0]
    final = projection.iloc[-1]
    sale_costs = float(final["property_value"]) * selling_cost_pct / 100.0
    net_sale_proceeds = float(final["property_value"] - final["mortgage_balance"] - sale_costs)
    cumulative_cashflow = float(projection["annual_cashflow"].sum())
    total_profit = cumulative_cashflow + net_sale_proceeds - initial_cash
    annual_cashflows = [-initial_cash] + projection["annual_cashflow"].astype(float).tolist()
    annual_cashflows[-1] += net_sale_proceeds
    irr = _annual_irr(annual_cashflows)

    results = {
        "mortgage_amount": mortgage,
        "ltv": 100.0 * mortgage / purchase_price,
        "monthly_mortgage_payment": monthly_mortgage_payment(
            mortgage, mortgage_rate_pct, mortgage_term_years, repayment_type
        ),
        "initial_cash_invested": initial_cash,
        "gross_yield": 100.0 * monthly_rent * 12.0 / purchase_price,
        "net_operating_yield": 100.0 * float(first["noi"]) / purchase_price,
        "cash_on_cash": 100.0 * float(first["annual_cashflow"]) / initial_cash if initial_cash > 0 else np.nan,
        "year_1_roe": float(first["roe"]),
        "future_property_value": float(final["property_value"]),
        "remaining_mortgage": float(final["mortgage_balance"]),
        "exit_equity_before_sale_costs": float(final["equity"]),
        "net_sale_proceeds": net_sale_proceeds,
        "cumulative_cashflow": cumulative_cashflow,
        "total_profit": total_profit,
        "total_roi": 100.0 * total_profit / initial_cash if initial_cash > 0 else np.nan,
        "annualised_roi": 100.0 * irr if pd.notna(irr) else np.nan,
        "total_interest_paid": float(projection["interest_paid"].sum()),
        "total_principal_repaid": float(projection["principal_repaid"].sum()),
    }
    return results, projection


def area_key(value: object) -> str:
    s = str(value).strip().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def pct_change_over_months(series: pd.Series, months: int) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) <= months:
        return np.nan
    current = float(s.iloc[-1])
    prior = float(s.iloc[-1 - months])
    if prior == 0:
        return np.nan
    return 100.0 * (current / prior - 1.0)


def latest_snapshot(history: pd.DataFrame, value_col: str, cohort_col: str, cohort: str) -> pd.DataFrame:
    d = history.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d[d[cohort_col].eq(cohort)].dropna(subset=["date", value_col])
    d = d.sort_values(["area_code", "date"])
    return d.groupby("area_code", as_index=False).tail(1)


def growth_table(history: pd.DataFrame, value_col: str, cohort_col: str, cohort: str) -> pd.DataFrame:
    d = history.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d[d[cohort_col].eq(cohort)].dropna(subset=["date", value_col])
    rows = []
    for code, g in d.sort_values("date").groupby("area_code"):
        g = g.sort_values("date")
        rows.append({
            "area_code": code,
            "price_growth_1y" if value_col == "avg_price" else "rent_growth_1y": pct_change_over_months(g[value_col], 12),
            "price_growth_3y" if value_col == "avg_price" else "rent_growth_3y": pct_change_over_months(g[value_col], 36),
            "price_growth_5y" if value_col == "avg_price" else "rent_growth_5y": pct_change_over_months(g[value_col], 60),
        })
    return pd.DataFrame(rows)


def build_market_snapshot(
    price_history: pd.DataFrame,
    rent_history: pd.DataFrame,
    property_type: str,
    ltv: float,
    mortgage_rate: float,
    running_cost_pct: float,
    void_pct: float,
) -> pd.DataFrame:
    p = latest_snapshot(price_history, "avg_price", "property_type", property_type)
    r = latest_snapshot(rent_history, "monthly_rent", "property_type", property_type)
    pg = growth_table(price_history, "avg_price", "property_type", property_type)
    rg = growth_table(rent_history, "monthly_rent", "property_type", property_type)

    cols_p = [c for c in ["area_code", "area", "region", "date", "avg_price", "sales_volume"] if c in p.columns]
    cols_r = [c for c in ["area_code", "date", "monthly_rent"] if c in r.columns]
    out = p[cols_p].merge(r[cols_r], on="area_code", how="inner", suffixes=("_price", "_rent"))
    out = out.merge(pg, on="area_code", how="left").merge(rg, on="area_code", how="left")

    out["annual_rent"] = out["monthly_rent"] * 12.0
    out["gross_yield"] = 100.0 * out["annual_rent"] / out["avg_price"]
    effective_rent = out["annual_rent"] * (1.0 - void_pct / 100.0)
    out["net_operating_income"] = effective_rent - out["avg_price"] * running_cost_pct / 100.0
    out["net_yield"] = 100.0 * out["net_operating_income"] / out["avg_price"]
    out["loan"] = out["avg_price"] * ltv / 100.0
    out["annual_interest"] = out["loan"] * mortgage_rate / 100.0
    out["annual_cashflow"] = out["net_operating_income"] - out["annual_interest"]
    out["monthly_cashflow"] = out["annual_cashflow"] / 12.0
    out["equity"] = out["avg_price"] - out["loan"]
    out["cash_on_cash"] = 100.0 * out["annual_cashflow"] / out["equity"].replace(0, np.nan)
    out["icr"] = out["annual_rent"] / out["annual_interest"].replace(0, np.nan)
    out["break_even_rate"] = 100.0 * out["net_operating_income"] / out["loan"].replace(0, np.nan)

    components = {
        "gross_yield": 0.28,
        "price_growth_3y": 0.22,
        "rent_growth_1y": 0.15,
        "rent_growth_3y": 0.10,
        "monthly_cashflow": 0.15,
        "icr": 0.10,
    }
    score = pd.Series(0.0, index=out.index)
    used = 0.0
    for col, weight in components.items():
        if col in out.columns and out[col].notna().sum() >= 10:
            score += out[col].rank(pct=True, na_option="bottom") * weight
            used += weight
    out["investment_score"] = 100.0 * score / used if used else np.nan
    return out


def area_history(
    price_history: pd.DataFrame,
    rent_history: pd.DataFrame,
    area_code: str,
    property_type: str,
    ltv: float,
    mortgage_rates: pd.DataFrame | None = None,
    running_cost_pct: float = 1.25,
    void_pct: float = 5.0,
) -> pd.DataFrame:
    p = price_history[(price_history.area_code == area_code) & (price_history.property_type == property_type)][["date", "avg_price"]].copy()
    r = rent_history[(rent_history.area_code == area_code) & (rent_history.property_type == property_type)][["date", "monthly_rent"]].copy()
    p["date"] = pd.to_datetime(p["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    r["date"] = pd.to_datetime(r["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    h = p.merge(r, on="date", how="outer").sort_values("date")
    # Interpolate only between genuine observations. Never extrapolate a single/current
    # cohort rent backwards through history, which would fabricate historical yields.
    h["avg_price"] = h["avg_price"].interpolate(limit_area="inside")
    if h["monthly_rent"].notna().sum() >= 2:
        h["monthly_rent"] = h["monthly_rent"].interpolate(limit_area="inside")
    h["gross_yield"] = 100 * (h.monthly_rent * 12) / h.avg_price
    h["annual_rent"] = h.monthly_rent * 12
    h["noi"] = h.annual_rent * (1 - void_pct / 100) - h.avg_price * running_cost_pct / 100

    if mortgage_rates is not None and not mortgage_rates.empty:
        m = mortgage_rates.copy()
        m["date"] = pd.to_datetime(m["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        m = m.dropna().sort_values("date").drop_duplicates("date", keep="last")
        h = pd.merge_asof(h.sort_values("date"), m[["date", "mortgage_rate_75ltv_2y"]].sort_values("date"), on="date", direction="backward")
        h["monthly_cashflow"] = (h.noi - h.avg_price * ltv / 100 * h.mortgage_rate_75ltv_2y / 100) / 12
    return h


def validate_bedroom_sales(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "No bedroom-level sales enrichment file is installed."
    try:
        df = pd.read_csv(path, nrows=10)
    except Exception as exc:
        return False, f"Could not read bedroom sales enrichment: {exc}"
    required = {"date", "area_code", "area", "bedrooms", "avg_price"}
    missing = required - set(df.columns)
    if missing:
        return False, "Bedroom sales file is missing: " + ", ".join(sorted(missing))
    return True, "Bedroom-level sales enrichment available."


def load_geojson(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
