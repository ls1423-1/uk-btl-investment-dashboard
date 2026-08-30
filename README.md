# UK Buy-to-Let Intelligence

A real-data, map-first Streamlit dashboard for screening UK buy-to-let markets by local authority and matched property cohort. It combines sale prices, rents, growth, yields, financing costs, cash flow, and historical trends. It does not generate demo observations or backfill a current rent into the past.

## Install and run on macOS

Open Terminal in the extracted project folder (or replace the example path below with the folder you extracted):

```bash
cd /path/to/uk_btl_dashboard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python fetch_real_data.py
python -m streamlit run app.py
```

The refresh is a network-heavy first build. Normal dashboard reruns use the generated local files in `data/`.

## Official data sources

- HM Land Registry UK House Price Index: monthly prices and growth-ready history by property type.
- Office for National Statistics Price Index of Private Rents (PIPR): observed monthly rent, rental index, and annual rent change by property type and bedroom count where published.
- Bank of England quoted 75% LTV two-year fixed mortgage series.
- ONS Open Geography local-authority boundaries.
- HM Land Registry Price Paid Data: optional current-year transaction/postcode summary.

The ONS downloader resolves the newest workbook listed on the official dataset page. It also saves `data/pipr_latest.xlsx` and `data/ons_workbook_sheets.txt` so a future schema change is diagnosable.

## Cohort methodology

Property-type yields match the same cohort on both sides of the calculation:

- Overall
- Detached
- Semi-detached
- Terraced
- Flat/maisonette

ONS bedroom rents are retained as genuine history for 1, 2, 3, and 4+ bedrooms. HM Land Registry Price Paid Data has no bedroom field, so exact bedroom-level sale prices or yields are disabled unless a genuine enriched `data/bedroom_sales_history.csv` is supplied. The dashboard does not infer bedrooms from property type.

## Refresh outputs

- `data/hpi_history.csv`
- `data/rent_history.csv`
- `data/rent_bedroom_history.csv`
- `data/boe_mortgage_rates.csv`
- `data/lad_boundaries.geojson`
- `data/ppd_postcode_current_year.csv` (optional)

The rent history includes source and observation-status fields. The ONS workbook itself begins in January 2015; earlier monthly bedroom/property-type rent observations are not fabricated.

## Dashboard features

- Local-authority choropleth by investment score, yield, growth, cash flow, or financing resilience.
- Matched-cohort price, rent, yield, and cash-flow histories.
- 1-, 3-, and 5-year growth metrics where enough history exists.
- Adjustable LTV, mortgage rate, running costs, and void assumptions.
- Area ranking, side-by-side comparison, and mortgage-rate stress testing.
- Latest bedroom-rent comparison, with exact bedroom-yield analysis activated only by defensible sales enrichment.
- Location-filtered investment calculator prefilled from the selected area's matched price and rent.
- Repayment and interest-only mortgage modelling with deposit, financed amount, amortisation, value/rent growth, exit proceeds, yield, ROE, cash-on-cash return, and total/annualised ROI.

## Validation and tests

Run:

```bash
source .venv/bin/activate
python -m pytest
```

The ingestion rejects malformed workbook downloads, invalid dates/rents, and a result that lacks meaningful England/Wales local-area coverage. Tests cover the live ONS measure-family schema, detached/semi-detached ambiguity, mortgage calculations, amortisation, and investment returns.

## Streamlit Community Cloud deployment

This repository is prepared to deploy with `app.py` as its entrypoint. The processed HPI, rent, mortgage-rate, and boundary files under `data/` are committed so the hosted app does not perform a large network refresh during startup. Reproducible raw downloads, local virtual environments, diagnostics, and optional private enrichment are excluded by `.gitignore`.

At [share.streamlit.io](https://share.streamlit.io):

1. Connect the GitHub account that administers this repository.
2. Create an app and select this repository's `main` branch.
3. Enter `app.py` as the entrypoint.
4. In advanced settings, select Python 3.11.
5. Choose the desired `streamlit.app` subdomain and deploy.

The committed `requirements.txt` pins the dependency versions used for local validation. Do not commit `.streamlit/secrets.toml`.

## Limitations

- HPI values and the newest observations can be provisional and revised.
- ONS PIPR coverage and release lags differ across UK nations and geographies.
- Postcode Price Paid summaries provide sales/liquidity context; local-authority rents are not presented as postcode-specific rents.
- Cash flow is a configurable research estimate, excludes personal taxation, and is not investment advice.
