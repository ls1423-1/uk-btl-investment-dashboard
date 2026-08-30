from __future__ import annotations

from pathlib import Path
import html
import io
import re
import sys
from urllib.parse import urljoin
import requests
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

HPI_FULL_URL = "https://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/UK-HPI-full-file-2026-06.csv"
PIPR_DATASET_URL = "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/priceindexofprivaterentsukmonthlypricestatistics"
PIPR_FALLBACK_URL = "https://www.ons.gov.uk/file?uri=%2Feconomy%2Finflationandpriceindices%2Fdatasets%2Fpriceindexofprivaterentsukmonthlypricestatistics%2F19august2026%2Fpriceindexofprivaterentsukmonthlypricestatistics.xlsx"
BOE_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2004&Dateto=now&SeriesCodes=IUMBV34&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
BOUNDARY_URL = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Local_Authority_Districts_December_2024_Boundaries_UK_BUC/FeatureServer/0/query"
PPD_YEAR_URL = "https://price-paid-data.publicdata.landregistry.gov.uk/pp-2026.csv"
HEADERS = {"User-Agent": "UK-BTL-dashboard/0.3 (research project; public data downloader)"}


def get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=180, **kwargs)
    r.raise_for_status()
    return r


def _latest_ons_workbook_url() -> str:
    """Resolve the current PIPR workbook instead of pinning every refresh to one release."""
    try:
        page = get(PIPR_DATASET_URL)
        for href in re.findall(r"href=[\"']([^\"']+)[\"']", page.text, flags=re.IGNORECASE):
            href = html.unescape(href)
            if "/file?uri=" in href.lower() and ".xlsx" in href.lower():
                return urljoin(PIPR_DATASET_URL, href)
    except requests.RequestException as exc:
        print(f"  Could not resolve the latest ONS PIPR edition ({exc}); using the August 2026 fallback.")
    return PIPR_FALLBACK_URL


def fetch_hpi_history() -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(get(HPI_FULL_URL).content))
    colmap = {c.lower().replace(" ", ""): c for c in raw.columns}
    def find(*names: str) -> str | None:
        for n in names:
            k = n.lower().replace(" ", "")
            if k in colmap:
                return colmap[k]
        return None

    date_col = find("Date")
    area_col = find("RegionName")
    code_col = find("AreaCode")
    if not (date_col and area_col and code_col):
        raise RuntimeError(f"Unexpected UK HPI columns: {list(raw.columns)}")

    price_fields = {
        "Overall": find("AveragePrice"),
        "Detached": find("DetachedPrice"),
        "Semi-detached": find("SemiDetachedPrice"),
        "Terraced": find("TerracedPrice"),
        "Flat/maisonette": find("FlatPrice"),
    }
    sales_col = find("SalesVolume")
    region_col = find("RegionName")
    frames = []
    for label, pcol in price_fields.items():
        if pcol is None:
            continue
        cols = [date_col, code_col, area_col, pcol] + ([sales_col] if sales_col else [])
        d = raw[cols].copy()
        d.columns = ["date", "area_code", "area", "avg_price"] + (["sales_volume"] if sales_col else [])
        d["property_type"] = label
        frames.append(d)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out.date, dayfirst=True, errors="coerce")
    out["avg_price"] = pd.to_numeric(out.avg_price, errors="coerce")
    if "sales_volume" in out:
        out["sales_volume"] = pd.to_numeric(out.sales_volume, errors="coerce")
    out = out.dropna(subset=["date", "area_code", "avg_price"])
    # Keep LAD-like codes plus national/regional series; app joins LAD codes to boundaries.
    return out.sort_values(["area_code", "property_type", "date"])


def _clean_header(v: object) -> str:
    if pd.isna(v):
        return ""
    return re.sub(r"\s+", " ", str(v).strip())


def _norm(v: object) -> str:
    """Aggressive normalisation for ONS headers that change punctuation/casing."""
    return re.sub(r"[^a-z0-9]+", "", _clean_header(v).lower())


def _parse_date_like(v: object) -> pd.Timestamp | None:
    if isinstance(v, pd.Timestamp):
        d = v
    else:
        s = _clean_header(v)
        if not s:
            return None
        # Numeric observations in ONS workbooks are not Excel dates for our purposes.
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
            return None
        d = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(d) or d.year < 2000 or d.year > 2035:
        return None
    return pd.Timestamp(d).to_period("M").to_timestamp()


def _money(v: object) -> float | None:
    if pd.isna(v):
        return None
    s = str(v).strip().replace(",", "").replace("£", "").replace("\xa0", "")
    if s.lower() in {"", "x", "..", "...", "-", "na", "n/a"}:
        return None
    x = pd.to_numeric(s, errors="coerce")
    if pd.isna(x) or not (50 <= float(x) <= 20000):
        return None
    return float(x)


def _looks_like_area_code(v: object) -> bool:
    # ONS administrative / rental-market geography codes are normally one letter + 8 digits.
    return bool(re.fullmatch(r"[A-Z]\d{8}", _clean_header(v).upper()))


def _header_map(raw: pd.DataFrame, row: int, depth: int = 3) -> list[str]:
    """Build tolerant labels from stacked ONS headers without contaminating columns with sheet titles."""
    labels = []
    for c in range(raw.shape[1]):
        current = _clean_header(raw.iat[row, c])
        if current:
            labels.append(current)
            continue
        inherited = ""
        for r in range(row - 1, max(-1, row - depth), -1):
            inherited = _clean_header(raw.iat[r, c])
            if inherited:
                break
        labels.append(inherited)
    return labels


def _find_col(labels: list[str], aliases: tuple[str, ...]) -> int | None:
    nn = [_norm(x) for x in labels]
    aliases_n = [_norm(x) for x in aliases]
    # exact-ish aliases first, then contained aliases
    for i, v in enumerate(nn):
        if any(v == a for a in aliases_n):
            return i
    for i, v in enumerate(nn):
        if any(a and a in v for a in aliases_n):
            return i
    return None


TYPE_ALIASES = {
    "detached": "Detached",
    "detachedproperties": "Detached",
    "semidetached": "Semi-detached",
    "semidetachedproperties": "Semi-detached",
    "terraced": "Terraced",
    "terracedproperties": "Terraced",
    "flatormaisonette": "Flat/maisonette",
    "flatsandmaisonettes": "Flat/maisonette",
    "flatmaisonette": "Flat/maisonette",
    "flat": "Flat/maisonette",
    "allpropertytypes": "Overall",
    "allproperties": "Overall",
    "overall": "Overall",
    "all": "Overall",
}

BED_ALIASES = {
    "onebedroom": "1", "onebed": "1", "1bedroom": "1", "1bed": "1",
    "twobedrooms": "2", "twobed": "2", "2bedrooms": "2", "2bed": "2",
    "threebedrooms": "3", "threebed": "3", "3bedrooms": "3", "3bed": "3",
    "fourormorebedrooms": "4+", "fourormorebed": "4+", "4ormorebedrooms": "4+", "4ormorebed": "4+", "4bedroomsormore": "4+", "4bed": "4+", "4+bedrooms": "4+",
}


def _canonical_type(v: object) -> str | None:
    n = _norm(v)
    if n in TYPE_ALIASES:
        return TYPE_ALIASES[n]
    # Longest first is important: "semidetached" also contains "detached".
    for k, out in sorted(TYPE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if len(k) >= 6 and k in n:
            return out
    return None


def _canonical_bed(v: object) -> str | None:
    n = _norm(v)
    if n in BED_ALIASES:
        return BED_ALIASES[n]
    for k, out in sorted(BED_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if k in n:
            return out
    return None


def _measure_and_suffix(label: object) -> tuple[str | None, str]:
    """Split headers such as ``Rental price two bed`` into metric and cohort suffix."""
    value = _norm(label)
    prefixes = {
        "monthly_rent": ("averagemonthlyrent", "averagerentalprice", "rentalprice", "averagerent", "monthlyrent"),
        "annual_rent_change": ("annualpercentagechange", "annualchange"),
        "rent_index": ("rentalindex", "index"),
    }
    for metric, candidates in prefixes.items():
        for prefix in candidates:
            if value == prefix or value.startswith(prefix):
                return metric, value[len(prefix):]
    return None, ""


def _discover_measure_table(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Parse area/date rows containing repeated measure/cohort column families.

    The current PIPR workbook uses columns such as ``Rental price``, ``Rental
    price one bed`` and ``Rental price detached``. This is neither a conventional
    long table nor an area-by-month matrix, so it needs explicit measure-family
    discovery to retain the complete cohort histories.
    """
    header_row = None
    positions: tuple[int, int, int, int | None] | None = None
    labels: list[str] = []
    for h in range(min(100, len(raw))):
        candidate = [_clean_header(v) for v in raw.iloc[h].tolist()]
        date_i = _find_col(candidate, ("time period", "date", "month", "period"))
        code_i = _find_col(candidate, ("area code", "geography code", "ladcd"))
        name_i = _find_col(candidate, ("area name", "geography name", "local authority name"))
        region_i = _find_col(candidate, ("region or country name", "region name", "country name"))
        rent_columns = sum(_measure_and_suffix(v)[0] == "monthly_rent" for v in candidate)
        if date_i is not None and code_i is not None and name_i is not None and rent_columns >= 1:
            header_row = h
            positions = (date_i, code_i, name_i, region_i)
            labels = candidate
            break
    if header_row is None or positions is None:
        return None, None

    date_i, code_i, name_i, region_i = positions
    body = raw.iloc[header_row + 1:].reset_index(drop=True)
    dates = pd.to_datetime(body.iloc[:, date_i], errors="coerce").dt.to_period("M").dt.to_timestamp()
    codes = body.iloc[:, code_i].map(_clean_header).str.upper()
    names = body.iloc[:, name_i].map(_clean_header)
    regions = body.iloc[:, region_i].map(_clean_header) if region_i is not None else pd.Series("", index=body.index)
    base_mask = dates.notna() & codes.map(_looks_like_area_code) & names.ne("")

    columns: dict[tuple[str, str, str], int] = {}
    for i, label in enumerate(labels):
        metric, suffix = _measure_and_suffix(label)
        if metric is None:
            continue
        bedroom = _canonical_bed(suffix)
        property_type = None if bedroom else (_canonical_type(suffix) if suffix else "Overall")
        if bedroom:
            columns[("bedroom", bedroom, metric)] = i
        elif property_type:
            columns[("property", property_type, metric)] = i

    def numeric_column(i: int | None) -> pd.Series:
        if i is None:
            return pd.Series(np.nan, index=body.index, dtype=float)
        values = body.iloc[:, i].astype(str).str.replace(",", "", regex=False).str.replace("£", "", regex=False)
        return pd.to_numeric(values, errors="coerce")

    property_frames: list[pd.DataFrame] = []
    bedroom_frames: list[pd.DataFrame] = []
    cohorts = sorted({(kind, cohort) for kind, cohort, metric in columns if metric == "monthly_rent"})
    for kind, cohort in cohorts:
        rents = numeric_column(columns.get((kind, cohort, "monthly_rent")))
        valid = base_mask & rents.between(50, 20000, inclusive="both")
        if not valid.any():
            continue
        frame = pd.DataFrame({
            "date": dates[valid],
            "area_code": codes[valid],
            "area": names[valid],
            "region": regions[valid],
            "monthly_rent": rents[valid],
            "rent_index": numeric_column(columns.get((kind, cohort, "rent_index")))[valid],
            "annual_rent_change": numeric_column(columns.get((kind, cohort, "annual_rent_change")))[valid],
            "source": "ONS PIPR",
            "observation_status": "observed",
        })
        if kind == "property":
            frame["property_type"] = cohort
            property_frames.append(frame)
        else:
            frame["bedrooms"] = cohort
            bedroom_frames.append(frame)

    properties = pd.concat(property_frames, ignore_index=True) if property_frames else None
    bedrooms = pd.concat(bedroom_frames, ignore_index=True) if bedroom_frames else None
    return properties, bedrooms


def _discover_long_ons(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Parse common ONS long layouts: one row per area/date[/cohort]."""
    best_rent: list[tuple] = []
    best_bed: list[tuple] = []
    max_header = min(100, max(0, len(raw) - 1))
    for h in range(max_header):
        labels = _header_map(raw, h, depth=4)
        code_i = _find_col(labels, ("area code", "areacode", "geography code", "geographycode", "geo code", "geocode", "lad code", "ladcd"))
        name_i = _find_col(labels, ("area name", "areaname", "area", "geography name", "geographyname", "local authority", "local authority name", "region name", "name"))
        date_i = _find_col(labels, ("date", "month", "time period", "timeperiod", "period", "time"))
        rent_i = _find_col(labels, ("average monthly rent", "average rent", "monthly rent", "rental price", "average rental price", "price level", "pricelevels", "average price", "price"))
        type_i = _find_col(labels, ("property type", "propertytype", "dwelling type", "dwellingtype"))
        bed_i = _find_col(labels, ("bedroom", "bedrooms", "number of bedrooms", "bedroom number", "bedroom category"))
        if code_i is None or name_i is None or date_i is None or rent_i is None:
            continue
        rent_rows, bed_rows = [], []
        for _, rr in raw.iloc[h + 1:].iterrows():
            code = _clean_header(rr.iloc[code_i]).upper()
            name = _clean_header(rr.iloc[name_i])
            dt = _parse_date_like(rr.iloc[date_i])
            val = _money(rr.iloc[rent_i])
            if not _looks_like_area_code(code) or not name or dt is None or val is None:
                continue
            typ = _canonical_type(rr.iloc[type_i]) if type_i is not None else "Overall"
            bed = _canonical_bed(rr.iloc[bed_i]) if bed_i is not None else None
            if typ:
                rent_rows.append((dt, code, name, typ, val))
            elif type_i is None:
                rent_rows.append((dt, code, name, "Overall", val))
            if bed:
                bed_rows.append((dt, code, name, bed, val))
        if len(rent_rows) > len(best_rent):
            best_rent = rent_rows
        if len(bed_rows) > len(best_bed):
            best_bed = bed_rows
    rents = pd.DataFrame(best_rent, columns=["date", "area_code", "area", "property_type", "monthly_rent"]) if best_rent else None
    beds = pd.DataFrame(best_bed, columns=["date", "area_code", "area", "bedrooms", "monthly_rent"]) if best_bed else None
    return rents, beds


def _discover_wide_ons(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Parse ONS wide layouts: areas on rows, dates/cohorts in columns or dates on rows, areas in columns."""
    best_rent: list[tuple] = []
    best_bed: list[tuple] = []

    # A) Area rows, date columns. Search for a column densely populated with ONS codes.
    code_counts = []
    for c in range(raw.shape[1]):
        cnt = int(raw.iloc[: min(len(raw), 5000), c].map(_looks_like_area_code).sum())
        if cnt >= 20:
            code_counts.append((cnt, c))
    for _, code_i in sorted(code_counts, reverse=True)[:4]:
        code_rows = [r for r in range(len(raw)) if _looks_like_area_code(raw.iat[r, code_i])]
        if not code_rows:
            continue
        first = min(code_rows)
        # name is normally immediately next to code; otherwise choose a text-heavy nearby column.
        candidates = [c for c in range(max(0, code_i - 2), min(raw.shape[1], code_i + 4)) if c != code_i]
        name_i = None
        for c in candidates:
            good = sum(bool(_clean_header(raw.iat[r, c])) and not _looks_like_area_code(raw.iat[r, c]) for r in code_rows[:100])
            if good >= min(15, len(code_rows[:100]) // 2):
                name_i = c
                break
        if name_i is None:
            continue
        # inspect several rows immediately above data for dates and cohort labels.
        for h in range(max(0, first - 8), first):
            date_cols = []
            for c in range(raw.shape[1]):
                dt = None
                for rr in range(max(0, h - 3), h + 1):
                    dt = _parse_date_like(raw.iat[rr, c]) or dt
                if dt is not None:
                    date_cols.append((c, dt))
            if len(date_cols) < 6:
                continue
            rent_rows = []
            for r in code_rows:
                code = _clean_header(raw.iat[r, code_i]).upper()
                name = _clean_header(raw.iat[r, name_i])
                if not name:
                    continue
                row_context = " ".join(_clean_header(raw.iat[r, c]) for c in range(min(raw.shape[1], 8)))
                typ = _canonical_type(row_context) or "Overall"
                bed = _canonical_bed(row_context)
                for c, dt in date_cols:
                    val = _money(raw.iat[r, c])
                    if val is None:
                        continue
                    if bed:
                        best_bed.append((dt, code, name, bed, val))
                    else:
                        rent_rows.append((dt, code, name, typ, val))
            if len(rent_rows) > len(best_rent):
                best_rent = rent_rows

    # B) Date rows, geography columns. This occurs in chart-oriented ONS sheets.
    date_cols_dense = []
    for c in range(raw.shape[1]):
        vals = [_parse_date_like(v) for v in raw.iloc[: min(len(raw), 5000), c]]
        cnt = sum(v is not None for v in vals)
        if cnt >= 12:
            date_cols_dense.append((cnt, c))
    for _, date_i in sorted(date_cols_dense, reverse=True)[:3]:
        date_rows = [(r, _parse_date_like(raw.iat[r, date_i])) for r in range(len(raw))]
        date_rows = [(r, d) for r, d in date_rows if d is not None]
        if len(date_rows) < 12:
            continue
        first = min(r for r, _ in date_rows)
        # Find ONS codes in the header block above the first date.
        for code_row in range(max(0, first - 12), first):
            geo_cols = [(c, _clean_header(raw.iat[code_row, c]).upper()) for c in range(raw.shape[1]) if _looks_like_area_code(raw.iat[code_row, c])]
            if len(geo_cols) < 5:
                continue
            name_row = None
            for nr in range(max(0, code_row - 3), min(first, code_row + 4)):
                good = sum(bool(_clean_header(raw.iat[nr, c])) and not _looks_like_area_code(raw.iat[nr, c]) for c, _ in geo_cols)
                if good >= min(5, len(geo_cols)):
                    name_row = nr
                    break
            rent_rows = []
            for c, code in geo_cols:
                name = _clean_header(raw.iat[name_row, c]) if name_row is not None else code
                header_context = " ".join(_clean_header(raw.iat[r, c]) for r in range(max(0, code_row - 5), first))
                typ = _canonical_type(header_context) or "Overall"
                bed = _canonical_bed(header_context)
                for r, dt in date_rows:
                    val = _money(raw.iat[r, c])
                    if val is None:
                        continue
                    if bed:
                        best_bed.append((dt, code, name, bed, val))
                    else:
                        rent_rows.append((dt, code, name, typ, val))
            if len(rent_rows) > len(best_rent):
                best_rent = rent_rows

    rents = pd.DataFrame(best_rent, columns=["date", "area_code", "area", "property_type", "monthly_rent"]) if best_rent else None
    beds = pd.DataFrame(best_bed, columns=["date", "area_code", "area", "bedrooms", "monthly_rent"]) if best_bed else None
    return rents, beds


def _discover_current_matrix(raw: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Extract latest property-type / bedroom price-level matrices even when they have no date column."""
    best_types, best_beds = [], []
    for h in range(min(120, len(raw))):
        labels = _header_map(raw, h, depth=4)
        code_i = _find_col(labels, ("area code", "geography code", "areacode", "geographycode", "ladcd"))
        name_i = _find_col(labels, ("area name", "geography name", "areaname", "geographyname", "area", "local authority", "name"))
        if code_i is None or name_i is None:
            continue
        tcols = [(i, _canonical_type(x)) for i, x in enumerate(labels)]
        tcols = [(i, x) for i, x in tcols if x and x != "Overall"]
        bcols = [(i, _canonical_bed(x)) for i, x in enumerate(labels)]
        bcols = [(i, x) for i, x in bcols if x]
        tr, br = [], []
        for _, rr in raw.iloc[h + 1:].iterrows():
            code = _clean_header(rr.iloc[code_i]).upper()
            name = _clean_header(rr.iloc[name_i])
            if not _looks_like_area_code(code) or not name:
                continue
            for i, typ in tcols:
                val = _money(rr.iloc[i])
                if val is not None:
                    tr.append((code, name, typ, val))
            for i, bed in bcols:
                val = _money(rr.iloc[i])
                if val is not None:
                    br.append((code, name, bed, val))
        if len(tr) > len(best_types): best_types = tr
        if len(br) > len(best_beds): best_beds = br
    types = pd.DataFrame(best_types, columns=["area_code", "area", "property_type", "monthly_rent"]) if best_types else None
    beds = pd.DataFrame(best_beds, columns=["area_code", "area", "bedrooms", "monthly_rent"]) if best_beds else None
    return types, beds


def fetch_rent_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    workbook_url = _latest_ons_workbook_url()
    response = get(workbook_url)
    content = response.content
    if not content.startswith(b"PK"):
        raise RuntimeError(
            f"ONS PIPR download was not an Excel workbook (content type: {response.headers.get('content-type', 'unknown')})."
        )
    workbook_path = DATA / "pipr_latest.xlsx"
    workbook_path.write_bytes(content)
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine="openpyxl")

    rent_candidates: list[pd.DataFrame] = []
    bed_candidates: list[pd.DataFrame] = []
    current_types: list[pd.DataFrame] = []
    current_beds: list[pd.DataFrame] = []
    diagnostics = []

    for name, raw in sheets.items():
        measure_r, measure_b = _discover_measure_table(raw)
        # The measure-family table is authoritative when present. Generic fallbacks
        # remain for older/future ONS layouts, but are not combined with it because
        # that could overwrite matched cohort histories.
        if measure_r is not None or measure_b is not None:
            long_r = long_b = wide_r = wide_b = cur_t = cur_b = None
        else:
            long_r, long_b = _discover_long_ons(raw)
            wide_r, wide_b = _discover_wide_ons(raw)
            cur_t, cur_b = _discover_current_matrix(raw)
        for x in (measure_r, long_r, wide_r):
            if x is not None and not x.empty: rent_candidates.append(x)
        for x in (measure_b, long_b, wide_b):
            if x is not None and not x.empty: bed_candidates.append(x)
        if cur_t is not None and not cur_t.empty: current_types.append(cur_t)
        if cur_b is not None and not cur_b.empty: current_beds.append(cur_b)
        diagnostics.append(
            f"{name}: shape={raw.shape}; measure_r={0 if measure_r is None else len(measure_r)}; "
            f"measure_b={0 if measure_b is None else len(measure_b)}; long_r={0 if long_r is None else len(long_r)}; "
            f"wide_r={0 if wide_r is None else len(wide_r)}; long_b={0 if long_b is None else len(long_b)}; "
            f"wide_b={0 if wide_b is None else len(wide_b)}; current_types={0 if cur_t is None else len(cur_t)}; "
            f"current_beds={0 if cur_b is None else len(cur_b)}"
        )

    (DATA / "ons_workbook_sheets.txt").write_text("\n".join(diagnostics))

    if not rent_candidates:
        raise RuntimeError(
            "Could not identify ONS local-area rent history after long/wide layout discovery. "
            "Please send data/ons_workbook_sheets.txt and data/pipr_latest.xlsx if this persists."
        )

    rents = pd.concat(rent_candidates, ignore_index=True)
    rents["date"] = pd.to_datetime(rents["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    rents = rents.dropna(subset=["date", "area_code", "monthly_rent"])
    rents = rents[rents["area_code"].map(_looks_like_area_code)]

    # Preserve one observed value per cohort/month, then append genuinely current-only matrices.
    rents = rents.sort_values(["area_code", "property_type", "date"]).drop_duplicates(["date", "area_code", "property_type"], keep="last")
    latest_date = rents["date"].max()

    if current_types and pd.notna(latest_date):
        ct = pd.concat(current_types, ignore_index=True).drop_duplicates(["area_code", "property_type"], keep="last")
        ct["date"] = latest_date
        rents = pd.concat([rents, ct[["date", "area_code", "area", "property_type", "monthly_rent"]]], ignore_index=True)
        rents = rents.drop_duplicates(["date", "area_code", "property_type"], keep="last")

    bedrooms_frames = []
    if bed_candidates:
        bh = pd.concat(bed_candidates, ignore_index=True)
        bh["date"] = pd.to_datetime(bh["date"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        bedrooms_frames.append(bh)
    if current_beds and pd.notna(latest_date):
        cb = pd.concat(current_beds, ignore_index=True).drop_duplicates(["area_code", "bedrooms"], keep="last")
        cb["date"] = latest_date
        bedrooms_frames.append(cb)
    bedrooms = pd.concat(bedrooms_frames, ignore_index=True) if bedrooms_frames else pd.DataFrame(columns=["date", "area_code", "area", "bedrooms", "monthly_rent"])
    if not bedrooms.empty:
        bedrooms = bedrooms.dropna(subset=["date", "area_code", "monthly_rent"]).drop_duplicates(["date", "area_code", "bedrooms"], keep="last")
        bedrooms = bedrooms.sort_values(["area_code", "bedrooms", "date"])

    # Critical integrity check: we need a meaningful local-authority history, not only national rows.
    local_codes = rents["area_code"].astype(str).str.match(r"^[EW]\d{8}$")
    if local_codes.sum() < 500:
        raise RuntimeError(
            f"ONS parser found only {int(local_codes.sum())} England/Wales local-area rent observations; refusing to build a misleading dashboard. "
            "Diagnostics are in data/ons_workbook_sheets.txt."
        )

    return rents.sort_values(["area_code", "property_type", "date"]), bedrooms

def fetch_boe() -> pd.DataFrame:
    text = get(BOE_URL).text
    df = pd.read_csv(io.StringIO(text))
    date_col = next((c for c in df.columns if "date" in str(c).lower()), df.columns[0])
    rate_col = next((c for c in df.columns if "iumbv34" in str(c).lower()), df.columns[-1])
    out = df[[date_col, rate_col]].copy()
    out.columns = ["date", "mortgage_rate_75ltv_2y"]
    out.date = pd.to_datetime(out.date, dayfirst=True, errors="coerce")
    out.mortgage_rate_75ltv_2y = pd.to_numeric(out.mortgage_rate_75ltv_2y, errors="coerce")
    return out.dropna().sort_values("date")


def fetch_boundaries() -> None:
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": "4326",
        "returnGeometry": "true",
        "f": "geojson",
    }
    r = get(BOUNDARY_URL, params=params)
    (DATA / "lad_boundaries.geojson").write_bytes(r.content)


def fetch_recent_ppd() -> None:
    """Download current-year PPD for postcode/town transaction drill-down.

    This is optional for the main app; failures do not block the official HPI/PIPR dashboard.
    """
    try:
        content = get(PPD_YEAR_URL).content
    except Exception as exc:
        print(f"  Price Paid Data optional download skipped: {exc}")
        return
    path = DATA / "ppd_2026.csv"
    path.write_bytes(content)
    names = ["transaction_id", "price", "date", "postcode", "property_type", "old_new", "tenure", "paon", "saon", "street", "locality", "town_city", "district", "county", "category", "record_status"]
    try:
        ppd = pd.read_csv(path, header=None, names=names, dtype=str)
        ppd["price"] = pd.to_numeric(ppd.price, errors="coerce")
        ppd["date"] = pd.to_datetime(ppd.date, errors="coerce")
        ppd["postcode_district"] = ppd.postcode.str.extract(r"^([A-Z]{1,2}\d[A-Z\d]?)", expand=False)
        summary = ppd.dropna(subset=["postcode_district", "price"]).groupby(["postcode_district", "property_type"]).agg(median_price=("price", "median"), transactions=("price", "size")).reset_index()
        summary.to_csv(DATA / "ppd_postcode_current_year.csv", index=False)
    except Exception as exc:
        print(f"  PPD downloaded but postcode summary failed: {exc}")


def main() -> None:
    print("Fetching UK HPI full history...")
    hpi = fetch_hpi_history()
    hpi.to_csv(DATA / "hpi_history.csv", index=False)
    print(f"  {len(hpi):,} area/cohort/month rows")

    print("Fetching ONS PIPR rent history and cohorts...")
    rents, bedrooms = fetch_rent_data()
    rents.to_csv(DATA / "rent_history.csv", index=False)
    bedrooms.to_csv(DATA / "rent_bedroom_history.csv", index=False)
    print(f"  {len(rents):,} property-type rent rows; {len(bedrooms):,} bedroom-rent history rows")

    print("Fetching Bank of England mortgage-rate history...")
    boe = fetch_boe()
    boe.to_csv(DATA / "boe_mortgage_rates.csv", index=False)
    print(f"  {len(boe):,} rate observations")

    print("Fetching ONS local-authority boundaries...")
    fetch_boundaries()
    print("  data/lad_boundaries.geojson")

    print("Fetching optional 2026 HMLR Price Paid Data for postcode drill-down...")
    fetch_recent_ppd()

    print("\nDone. Run: python -m streamlit run app.py")
    print("Note: exact bedroom-level sale-price/yield comparison needs a genuine bedroom sales enrichment; HMLR PPD does not contain bedrooms.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nDATA REFRESH FAILED: {exc}", file=sys.stderr)
        raise
