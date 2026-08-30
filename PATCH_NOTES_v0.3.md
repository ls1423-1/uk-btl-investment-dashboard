# v0.3 patch notes

Overlay these files onto the existing `uk_btl_dashboard` v0.2 repository, then rerun the data refresh.

## Added
- `btl_core.py`: cohort, growth, yield, score and historical cash-flow calculations.
- `data/bedroom_sales_history_TEMPLATE.csv`: schema for genuine bedroom-level sales enrichment.

## Replaced
- `app.py`
- `fetch_real_data.py`
- `requirements.txt`
- `README.md`

## New data cache produced by refresh
- `data/hpi_history.csv`
- `data/rent_history.csv`
- `data/rent_bedroom_current.csv`
- `data/boe_mortgage_rates.csv`
- `data/lad_boundaries.geojson`
- optional `data/ppd_2026.csv`
- optional `data/ppd_postcode_current_year.csv`

## Apply on macOS
From the parent directory containing your existing repo and the extracted patch:

```bash
cp -R uk_btl_dashboard_v0.3_patch/* "uk_btl_dashboard 2"/
cd "uk_btl_dashboard 2"
source .venv/bin/activate
python -m pip install -r requirements.txt
python fetch_real_data.py
python -m streamlit run app.py
```

Do not copy the template to `bedroom_sales_history.csv` with zero values. That filename is reserved for a genuine enriched sales dataset.
