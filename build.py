"""Apartment Atlas pipeline: Flatfox sale listings + BFS vacancy + SBB commute -> listings.json.

Run: uv run --no-project --python 3.13 python -I build.py
Stages cache to data/. To pull fresh listings: delete data/02_listings.json and data/04_commute.json.
"""
from concurrent.futures import ThreadPoolExecutor
import csv, json, math, re, statistics, sys, time, urllib.parse, urllib.request, zipfile
from pathlib import Path

D = Path(__file__).parent / "data"
BBOX = dict(east=9.15, west=7.95, north=47.75, south=47.0)  # ~1 h commuter belt around Zürich HB
HB = (47.37772, 8.54021)
UA = {"User-Agent": "apartment-atlas/0.1 (personal research)"}


def get(url, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            print("retry", url[:90], e, file=sys.stderr)
            time.sleep(5 * (i + 1))


def cached(name, fn):
    p = D / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    v = fn()
    p.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return v


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


# 1. Flatfox: map pins in the bbox (cap 1000), then full listings in batches
def fetch_listings():
    q = urllib.parse.urlencode({**BBOX, "max_count": 1000, "offer_type": "SALE", "object_category": "APARTMENT"})
    pins = get(f"https://flatfox.ch/api/v1/pin/?{q}")
    assert len(pins) < 1000, "pin cap hit: split the bbox"
    pks, out = [p["pk"] for p in pins], []
    for i in range(0, len(pks), 40):
        qs = "&".join(f"pk={k}" for k in pks[i:i + 40])
        out += get(f"https://flatfox.ch/api/v1/public-listing/?{qs}&limit=40")["results"]
        time.sleep(0.5)
    return out


def pmap(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


# 2. Gemeinde (BFS number): swisstopo's official postcode directory (data/plz/), nearest piece with the listing's postcode.
# The geo.admin identify API was too slow and flaky (30-70 s, timeouts) to call per listing.
def fetch_gemeinde(raw):
    with open(next((D / "plz").rglob("*.csv")), encoding="utf-8-sig") as f:
        rows = [(int(r["PLZ4"]), r["Gemeindename"], int(r["BFS-Nr"]), r["Kantonskürzel"], float(r["N"]), float(r["E"]))
                for r in csv.DictReader(f, delimiter=";")]
    res = {}
    for l in raw:
        pt, z = (l["latitude"], l["longitude"]), int(l["zipcode"] or 0)
        cand = [r for r in rows if r[0] == z] or rows
        r = min(cand, key=lambda r: km(pt, (r[4], r[5])))
        res[str(l["pk"])] = [r[2], r[1], r[3]]
    return res


# 3. Commute: nearest stop, then fastest weekday-morning trip from it to Zürich HB
def nearest_stop(pt):
    st = [s for s in get(f"https://transport.opendata.ch/v1/locations?x={pt[0]}&y={pt[1]}&type=station")["stations"] if s.get("id")]
    return st[0] if st else None


def trip(sid):
    q = urllib.parse.urlencode({"from": sid, "to": "8503000", "date": "2026-12-01", "time": "07:30", "limit": 4})
    cons = get(f"https://transport.opendata.ch/v1/connections?{q}")["connections"]
    durs = [int(d[:2]) * 1440 + int(d[3:5]) * 60 + int(d[6:8]) for d in (c["duration"] for c in cons) if d]
    return [min(durs) if durs else None, min((c["transfers"] for c in cons), default=None)]


def fetch_commute(pts):
    stops = dict(zip(pts, pmap(nearest_stop, pts.values(), 4)))
    ids = sorted({s["id"] for s in stops.values() if s})
    trips = dict(zip(ids, pmap(trip, ids, 4)))
    return {k: s and [s["name"], round(km(pts[k], (s["coordinate"]["x"], s["coordinate"]["y"])), 2), *trips[s["id"]]]
            for k, s in stops.items()}


# 4. BFS vacancy 2026 (stats.swiss SDMX, all communes): rate overall + vacant flats for sale
def load_vacancy():
    v = {}
    with open(D / "lwz.csv", encoding="utf-8") as f:
        for r in csv.reader(f):
            if len(r) < 8 or not r[1][:1].isdigit() or not r[2].startswith("_T") or not r[7]:
                continue
            gde, typ, meas = int(r[1].split(":")[0]), r[3].split(":")[0], r[4].split(":")[0]
            e = v.setdefault(gde, {})
            if typ == "_T":
                e["rate" if meas == "RATE" else "vacant"] = float(r[7])
            elif typ == "3" and meas == "OBS":
                e["for_sale"] = float(r[7])
    return v


YEAR = re.compile(r"(?:baujahr|erbaut|jahrgang|fertiggestellt|fertigstellung|built in|construit en|erstellt)\D{0,20}((?:18|19|20)\d\d)", re.I)


def year_of(l):
    if l.get("year_built"):
        return l["year_built"], "listing"
    m = YEAR.search(f"{l.get('description_title') or ''} {l.get('description') or ''}")
    if m and 1850 <= int(m[1]) <= 2028:
        return int(m[1]), "text"
    if re.search(r"\b(erstbezug|neubau|new build|neubauprojekt)\b", f"{l.get('description_title')} {l.get('description')}", re.I) and not l.get("year_renovated"):
        return 2026, "new"
    return None, None


SOURCES = {  # downloaded once, kept out of git
    "lwz.csv": ("https://disseminate.stats.swiss/rest/data/CH1.LWZ,DF_LWZ_1,1.1.0/all?lastNObservations=1&dimensionAtObservation=AllDimensions",
                {"Accept": "application/vnd.sdmx.data+csv;labels=both"}),
    "plz.zip": ("https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz_4326.csv.zip", {}),
}


def fetch_sources():
    D.mkdir(exist_ok=True)
    for name, (url, hdr) in SOURCES.items():
        if not (D / name).exists() and not (name == "plz.zip" and (D / "plz").exists()):
            print("downloading", name)
            with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **hdr}), timeout=300) as r:
                (D / name).write_bytes(r.read())
    if not (D / "plz").exists():
        zipfile.ZipFile(D / "plz.zip").extractall(D / "plz")


def main():
    fetch_sources()
    raw = cached("02_listings.json", fetch_listings)
    raw = [l for l in raw if l["offer_type"] == "SALE" and l.get("latitude") and l.get("price_display")]
    pts = {str(l["pk"]): (l["latitude"], l["longitude"]) for l in raw}
    gem = fetch_gemeinde(raw)  # local, no cache needed
    com = cached("04_commute.json", lambda: fetch_commute(pts))
    vac = load_vacancy()

    rows = []
    for l in raw:
        k = str(l["pk"])
        g, c = gem.get(k) or [None] * 3, com.get(k)
        y, ysrc = year_of(l)
        m2 = l.get("surface_living") or l.get("livingspace")
        door = None if not c or c[2] is None else round(c[1] * 12 + c[2])  # 12 min/km walk to the station + train
        rows.append(dict(id=l["pk"], url="https://flatfox.ch" + l["url"], title=l.get("description_title") or l["short_title"],
                         type=l.get("object_type"), price=l["price_display"], m2=m2,
                         rooms=float(l["number_of_rooms"]) if l.get("number_of_rooms") else None,
                         floor=l.get("floor"), year=y, ysrc=ysrc, reno=l.get("year_renovated"),
                         attrs=[a["name"] for a in l.get("attributes") or []],
                         lat=round(l["latitude"], 5), lon=round(l["longitude"], 5), zip=l.get("zipcode"), city=l.get("city"),
                         gde=g[0], gname=g[1], kt=g[2], km=round(km(pts[k], HB), 1),
                         station=c and c[0], walk=c and c[1], train=c and c[2], transfers=c and c[3], door=door,
                         vac=(vac.get(g[0]) or {}).get("rate"), vac_sale=(vac.get(g[0]) or {}).get("for_sale"),
                         img=l.get("cover_image"), published=l.get("published", "")[:10], agency=(l.get("agency") or {}).get("name")))
    # CHF/m² relative to the median of all listings (the sample is too thin for per-Gemeinde medians)
    ppm = [r["price"] / r["m2"] for r in rows if r["m2"]]
    med = statistics.median(ppm)
    for r in rows:
        r["ppm"] = round(r["price"] / r["m2"]) if r["m2"] else None
    out = dict(built=time.strftime("%Y-%m-%d"), median_ppm=round(med), vacancy_year=2026, rows=rows)
    (Path(__file__).parent / "listings.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    # self-check
    assert rows and all(r["gde"] for r in rows[:5]), "Gemeinde lookup failed"
    n = len(rows)
    print(f"{n} listings, median {med:.0f} CHF/m²")
    for f in ("m2", "floor", "year", "gde", "vac", "door"):
        print(f"  {f:6} {sum(r[f] is not None for r in rows)}/{n}")


if __name__ == "__main__":
    main()
