#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Launching UK BTL Dashboard v0.3.3 from: $(pwd)"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -r requirements.txt
if [ ! -f data/hpi_history.csv ] || [ ! -f data/rent_history.csv ] || [ ! -f data/lad_boundaries.geojson ]; then
  echo "Official v0.3 data cache missing; refreshing now..."
  python fetch_real_data.py
fi
python -m streamlit run app.py
