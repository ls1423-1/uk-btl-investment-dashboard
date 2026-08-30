# v0.3.2 parser hotfix

This hotfix replaces the brittle ONS PIPR workbook parser with layout discovery that supports:

- long-form ONS tables (area/date/cohort/value rows),
- wide tables with areas on rows and months on columns,
- chart-style tables with months on rows and geography codes on columns,
- looser ONS header naming (AreaCode, Area, Time period, Price, etc.),
- current property-type and bedroom matrices.

It also fixes a data-integrity bug in historical charts: a single latest rent observation is no longer extrapolated backwards through the entire house-price history. Historical yield/cash-flow points now require genuine historical rent support.

## Install over v0.3.1

Copy `fetch_real_data.py`, `btl_core.py`, and `app.py` into the root of the existing dashboard, replacing those files. Then run:

```bash
python fetch_real_data.py
python -m streamlit run app.py
```

If ONS changes the workbook again and parsing still fails, the script writes `data/ons_workbook_sheets.txt` with per-sheet parser counts and keeps `data/pipr_aug2026.xlsx` for diagnosis.
