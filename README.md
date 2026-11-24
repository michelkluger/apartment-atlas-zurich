# Apartment Atlas Zürich

**Live dashboard: https://michelkluger.github.io/apartment-atlas-zurich/**

Which apartment around Zürich should I buy? The dashboard ranks the apartments for sale within about an hour of Zürich HB. You set hard limits (budget, living area, rooms, commute, year built, floor) and say what matters most. Each listing then gets a 0–100 score against the others that pass. Click a flat to select it, click it again to open its ad.

## Data

| What | Source |
|---|---|
| Price, area, rooms, floor, year built, location | [Flatfox](https://flatfox.ch) public listings API |
| Empty-dwelling rate per Gemeinde, 1 June 2026 | [BFS Leerwohnungszählung](https://stats.swiss/vis?lc=de&df%5bds%5d=disseminate&df%5bid%5d=DF_LWZ_1&df%5bag%5d=CH1.LWZ) via stats.swiss |
| Gemeinde of each listing | [swisstopo postcode directory](https://data.geo.admin.ch/browser/index.html#/collections/ch.swisstopo-vd.ortschaftenverzeichnis_plz) |
| Commute to Zürich HB | [transport.opendata.ch](https://transport.opendata.ch) (SBB timetable) |
| Expected rent | ~3’500 unfurnished Flatfox rentals in the same area |

## Method

- **Door to door:** walking to the nearest stop at 12 min per km (straight line), plus the fastest of four trips from that stop to Zürich HB on a Tuesday at 07:30.
- **Gemeinde:** the nearest piece of the listing's postcode in the swisstopo directory.
- **Year built:** the listing field, or the ad text ("Baujahr 1998"). "Erstbezug" or "Neubau" without a year counts as 2026.
- **Expected rent:** the median net rent per m² of unfurnished rentals of similar size (−30% to +40%) within 1.5 km, times the flat's m². The radius widens up to 12 km until there are 8 comparables. Ads that give only a gross rent count at 90%, the net share seen in ads that give both. **Price-to-rent** = asking price ÷ yearly rent, i.e. how many years of rent the price equals.
- **Down payment back:** down payment ÷ (yearly net rent − mortgage interest − upkeep), with sliders for the down payment (default 20%), the rate (1.8%, a 10-year fixed in mid-2026) and upkeep (0.7% of the price a year). Nebenkosten are paid either way and cancel. Amortisation, Eigenmietwert tax, the interest deduction, purchase fees and the return the down payment would earn elsewhere are left out.
- **Score:** each criterion is a percentile among the listings that pass your limits. The weights then average them.

## Limits

- Flatfox only. Homegate and ImmoScout24 carry most sale listings but have no public API.
- Asking prices, not transaction prices. Ads without a price are left out.
- The vacancy rate covers all dwellings, both rented and owned.

## Files

- `index.html`: the dashboard
- `listings.json`: one row per listing, as the dashboard reads it
- `build.py`: the pipeline (Python standard library only). Run it with `uv run --no-project --python 3.13 python -I build.py`.
- `data/04_commute.json`, `data/05_rentals.json`: cached pipeline stages (rentals trimmed to location, m², rooms and net rent). The full Flatfox ads, the BFS CSV and the postcode directory are downloaded by `build.py` and not committed.

Listing data © the advertisers on Flatfox. Vacancy data © BFS. Postcode directory © swisstopo.
