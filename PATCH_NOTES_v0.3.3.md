# v0.3.3 ONS PIPR ingestion fix

- Parses the current ONS measure-family layout directly (`Rental price`, `Rental price one bed`, `Rental price detached`, and related index/growth columns).
- Preserves complete monthly histories for all five property cohorts and all four bedroom cohorts.
- Resolves the latest workbook from the official ONS dataset page, with a known-good fallback URL.
- Fixes semi-detached classification taking the shorter `detached` match.
- Records ONS rental index, annual rent change, source, and observed/modelled status.
- Keeps diagnostics and rejects downloads that are not real Excel workbooks.
- Adds parser regression tests based on the live workbook schema.
- Uses Plotly's current `choropleth_map` API while retaining compatibility with older Plotly releases.
- Adds a location-first investment calculator page using matched latest HMLR/ONS values and modelling yield, ROE, ROI, mortgage amortisation, growth, and exit proceeds.
