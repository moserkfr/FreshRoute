# FreshRoute

**Shelf-life-aware, explainable distribution planning for perishable produce.**

Every morning a produce hub has to decide which batches to dispatch, to which market, on which
truck, and what to hold back or discount. Decide badly and produce spoils at the hub, arrives too
old to sell, or floods a market that is already full. FreshRoute makes that decision with an
optimiser that respects shelf life, travel time, truck capacity, delivery windows, demand and price
forecasts, and it explains every recommendation in plain language.

It is evaluated against a documented baseline (today's practice: first-in-first-out to the nearest
market) on 11 scenarios with held-out seeds, and reports spoilage, demand fulfilment and net revenue
with 95% confidence intervals.

> Built for PS 03 (FreshRoute: Intelligent Decision Support for Perishable Produce Distribution).

---

## Results

Validation seeds 51–100 (50 instances per scenario, 14-day runs), paired with the baseline on the
same instances. Baseline **B1** = FIFO to the nearest market. 95% bootstrap confidence intervals.

| scenario | spoilage B1 | spoilage **ours** | fulfilment B1 | fulfilment **ours** | net revenue B1 | net revenue **ours** | Δ net revenue [95% CI] | win rate | violations ours / B1 |
|---|---|---|---|---|---|---|---|---|---|
| normal | 12.5% | **4.2%** | 31.1% | **33.4%** | ₹9.18M | **₹11.69M** | +₹2.51M [2.42, 2.59] | 100% | 0 / 87 |
| festival_peak | 13.3% | **5.5%** | 37.5% | **39.0%** | ₹7.67M | **₹9.65M** | +₹1.98M [1.91, 2.05] | 100% | 0 / 84 |
| harvest_glut | 16.2% | **7.0%** | 43.3% | **48.3%** | ₹4.49M | **₹6.17M** | +₹1.67M [1.59, 1.76] | 100% | 0 / 161 |
| scarcity | 11.8% | **3.9%** | 20.4% | **21.4%** | ₹7.94M | **₹10.91M** | +₹2.97M [2.88, 3.06] | 100% | 0 / 52 |
| heatwave | 17.2% | **4.5%** | 29.3% | **33.7%** | ₹8.37M | **₹11.47M** | +₹3.11M [3.01, 3.18] | 100% | 0 / 130 |
| monsoon | 15.7% | **3.4%** | 23.8% | **26.9%** | ₹6.76M | **₹9.35M** | +₹2.59M [2.53, 2.66] | 100% | 0 / 163 |
| lane_closure | 12.5% | **4.2%** | 31.1% | **33.5%** | ₹9.18M | **₹11.70M** | +₹2.52M [2.43, 2.61] | 100% | 0 / 87 |
| reefer_breakdown | 12.6% | **4.6%** | 31.1% | **33.3%** | ₹9.16M | **₹11.62M** | +₹2.46M [2.38, 2.54] | 100% | 0 / 90 |
| demand_spike | 12.5% | **4.1%** | 30.7% | **33.0%** | ₹9.18M | **₹11.73M** | +₹2.56M [2.47, 2.64] | 100% | 0 / 87 |
| price_crash | 12.5% | **4.1%** | 31.1% | **33.5%** | ₹9.18M | **₹11.71M** | +₹2.53M [2.44, 2.62] | 100% | 0 / 87 |
| grading_noise | 25.4% | **7.3%** | 24.4% | **32.5%** | ₹7.18M | **₹10.80M** | +₹3.62M [3.43, 3.82] | 100% | 0 / 607 |

- **Spoilage cut by roughly two thirds** in every scenario, **net revenue +₹1.7M to +₹3.6M per
  fortnight**, and FreshRoute beats the baseline on **every one of the 550 paired instances**.
- **Zero constraint violations.** Every plan is checked by an independent validator. B1 breaks
  shelf-life and buyer-quality rules because it ignores them.
- **Replanning** after a reefer breakdown recovers about ₹67k per run (95% CI ₹17k–₹116k) versus
  sticking to the morning plan.
- **Fulfilment** is low for every planner by design: destination demand is 3× the hub's supply,
  so about 33% is the ceiling. Reports also show the share of *possible* demand met.

These numbers are from the full run `eval_20261015_093844`. Since then the planner also prices in
the chance that a buyer rejects a load on quality, and the evaluation lets every planner sell down
its hub stock for 3 days after the last delivery. On tuning seeds this lowered our spoilage further
(4.5% → 3.9%) and raised net revenue. Run `python tasks.py eval` to reproduce; reports are written to
`reports/`.

Other planners in every report: **B2** chase the highest price, **B3** FEFO greedy
(shelf-life-aware heuristic), and **ours without the festival calendar** (ablation).

---

## How it works

```
 morning state ──► feasibility filter ──► MILP allocation ──► discount engine ──► validator ──► plan + reasons
 (batches, trucks,   (shelf-life chance      (which batch, where,    (clear at-risk        (independent       (why card,
  lanes, forecasts)   constraint, quality,    which truck, what to    stock with rules)      rule check)        runner-up)
                      windows, horizon)       hold)
```

1. **Shelf life.** Each batch has a remaining life (mean and spread) in reference days. Life is used
   up faster at higher temperature (Q10 rule) and faster still below a commodity's chilling
   threshold. Arrival quality decides the price band and whether a buyer accepts the load.
2. **Feasibility.** An option (batch → destination → truck) survives only if the batch is still
   sellable on arrival with 90% probability, its 10th-percentile arrival quality meets the buyer's
   minimum, reefers run a compatible temperature, and the lane is open. The trip uses the 90% travel
   time, so a load that may miss today's receiving window is valued as a next-morning sale.
3. **Optimisation (OR-Tools / SCIP).** A mixed-integer program maximises expected net revenue:
   revenue at the forecast price and arrival quality, minus truck costs, minus expected unsold kg
   (shipped kg are split into demand segments that sell with decreasing probability), minus the
   expected value of loads the buyer may reject. Holding is valued by what the stock can still earn
   in the next 3 days, but only in the market room fresh arrivals leave. Constraints: truck capacity,
   one destination per truck per day, one temperature per reefer.
4. **Discounts.** At-risk stock gets a disclosed discount ladder (0/10/20/35%); no discounting just
   before a festival.
5. **Validation and fallback.** An independent validator re-checks every allocation. If the solver
   fails, a heuristic plan is used and flagged.
6. **Explanations.** Every batch gets a reason: the decision, the runner-up option, the value
   difference, and the drivers (arrival freshness at 90% confidence, price, freight).

**Market model.** Prices and arrivals are fitted on real Agmarknet mandi data (2001–2025) for
Bengaluru, Mysuru, Hubballi, Hyderabad and Kolar: trend, seasonality, weekday effects, regimes, and
a festival calendar (Deepavali, Dasara, Ugadi, …) with festival demand uplifts. Forecast error on the
2025 hold-out year: WAPE 42% overall, 51% in festival windows.

**Network.** Hub at Kolar; 7 destinations (Bengaluru KR Market, Bengaluru retail DC, Mysuru,
Chennai Koyambedu, Hyderabad Bowenpally, Hubballi, Malur processing unit); real road distances
from OSRM; 6 ambient trucks (3–9 t) and 2 reefers. Six commodities: spinach, tomato, okra, mango,
cabbage, onion.

---

## The app

| page | what it shows |
|---|---|
| **Today's plan** | KPI cards vs B1, allocation table, "why" card for any batch, comparison table |
| **My stock** | a vendor enters their own stock: form, CSV upload, or **paste a WhatsApp message** (crates, bags, quintals; English / Hindi / Kannada names; "kal", "2 days old"). Life left is worked out from grade and harvest day, then the same planner plans it |
| **Map** | hub → destinations; line width = kg, colour = arrival freshness |
| **Market pulse** | price vs seasonal norm, 7-day momentum, festival radar, regime flag |
| **What-if** | break a truck, close a lane, spike demand, crash a price, add batches → replan + diff + KPI change |
| **Evaluation** | per-scenario results with confidence intervals, win rate, sensitivity tornado, replan vs static |
| **Settings** | risk appetite (chance-constraint level 80–97%), forecast mode |
| **Assumptions & sources** | every assumption in the configs with its source |

---

## Run it

Requirements: Python 3.11+ (3.13 tested), Node 18+. No API keys.

```bash
git clone https://github.com/moserkfr/FreshRoute && cd FreshRoute
python tasks.py setup     # virtualenv + Python deps (+ npm install in web/)
python tasks.py api       # API on http://localhost:8000  (docs: /docs)
python tasks.py web       # web app on http://localhost:5173  (second terminal)
```

Other tasks (`make <target>` works too where `make` exists):

| task | what it does |
|---|---|
| `python tasks.py test` | test suite (119 tests) |
| `python tasks.py lint` | ruff |
| `python tasks.py eval` | full evaluation: 11 scenarios × validation seeds × 5 planners, replan and sensitivity runs (`--jobs 8` for parallel workers) |
| `python tasks.py eval --hidden` | the one-time run on the locked hidden seeds (≥ 1000) |
| `python tasks.py gen` | generate a scenario instance |
| `python tasks.py fetch` / `fit` | re-download Agmarknet data and refit the market model (fitted files are committed, so this is optional) |
| `python tasks.py schemas` | export JSON schemas for the frontend types |

Runs are reproducible: every instance is generated from (scenario, seed), the planner stops on a
fixed node limit instead of wall-clock time, and each run records a fingerprint of its data.

---

## Evaluation protocol

- **Seeds.** Tuning 1–50 (all development and parameter choices), validation 51–100 (reported
  above), hidden ≥ 1000 (locked; the API refuses them).
- **Scenarios.** normal; festival_peak (Deepavali 2025); harvest_glut (+60% arrivals, −25% prices);
  scarcity (−35% supply, +40% prices); heatwave (+6 °C); monsoon (−20% supply, +30% travel time,
  delays twice as likely, noisier grading); lane_closure (farthest lane closed from day 3);
  reefer_breakdown (R1 down on day 2); demand_spike (+80% demand at one market, unannounced);
  price_crash (one market × commodity −30…−60%); grading_noise (shelf-life uncertainty ×2).
- **Simulation.** 14 days. True demand, prices, travel delays (20% of legs) and actual shelf life
  are drawn from the instance, not from the forecasts the planner sees. Each market sells at most
  its true demand; the rest is unsold. After the last delivery day every planner keeps dispatching
  its held stock for 3 days (no new stock), so leftovers end sold or spoiled.
- **Metrics.**
  - *Spoilage* = (spoiled at the hub + spoiled in transit + unsold or rejected at market) ÷ kg received.
  - *Fulfilment* = kg sold on delivery days ÷ true demand on those days.
  - *Net revenue* = sales − transport − handling − storage − disposal (₹). Discounts reduce sales.
  - *Violations* = validator findings (capacity, stock, shelf-life chance, buyer quality, windows, reefer compatibility).
- **Statistics.** Means with 95% bootstrap confidence intervals over seeds; paired differences
  and win rates against B1 on the same instances.
- **Sensitivity.** Each key assumption (Q10, shelf-life spread, delay probability, demand and price
  noise, elasticity, freight) is moved ±20% for the whole world, planners included.

---

## Key assumptions

All parameters live in `config/*.yaml`, each with a source or an explicit "assumption" note; the app's
*Assumptions & sources* page lists them.

- Hub storage 24 °C (shaded packhouse); reefer setpoints 2 °C (chilled) and 13 °C (cool, for mango).
- Shelf-life spread 10–20% of the mean depending on grade; grades A/B/C start with 100/90/75% of
  reference life.
- Price falls with arrival quality: full price above 60% quality, down to 70% at 30%, clearance at
  50% below that, unsellable below 15%.
- Disposal ₹1/kg; discounts lift sell-through 1.5× the discount, capped at +40%.
- Procurement cost is sunk; net revenue does not subtract it.
- Spinach uses green-onion price data (spinach is almost absent from Agmarknet); Chennai prices are
  Bengaluru wholesale × 1.10.
- No live mandi feed; prices come from the fitted model.

## Limitations

- The dataset is synthetic but grounded in real price data, distances and published shelf-life
  ranges; results are modelled outcomes, not field results.
- Shelf life at the hub assumes the stock sits at hub temperature since harvest; long-storage crops
  such as cured onion keep longer in reality.
- One hub, one dispatch wave per day, one destination per truck per day.

## Sources

- Post-harvest losses (NABCONS 2022, ICAR-CIPHET 2015), Lok Sabha AU1872: https://eparlib.nic.in/bitstream/123456789/2501891/1/AU1872.pdf
- Loss value ≈ ₹1.53 lakh crore per year: https://insights.dataful.in/articles/from-guava-to-milk-what-food-loss-reveals-about-indias-supply-chains
- Agmarknet mandi prices and arrivals (via India Data Portal / CEDA–Ashoka): https://agmarknet.ceda.ashoka.edu.in/README.html
- Storage temperature and shelf life (USDA Handbook 66 via UGA): https://l-webserver-prod.fcs.uga.edu/docs/Storage_of_Fresh_Produce.pdf
- Mango storage at 13 °C and chilling injury: https://www.itfnet.org/v1/2016/05/mango-post-harvest-processing/
- Q10 shelf-life model and 20% delay probability: https://arxiv.org/pdf/2511.05920
- Road freight cost in India: https://www.freightfox.ai/industry-insights/freightfox-insight-brief-indias-logistics-cost-landscape
- Reefer cost premium and availability: https://agrospectrumindia.com/news/88/34121/why-indias-fruits-and-vegetables-are-dying-on-road.html
- Tomato/onion/potato price seasonality (RBI): https://rbi.org.in/scripts/PublicationsView.aspx?id=22723
- Festival dates (kalagana, MIT licence): https://pypi.org/project/kalagana/
- Road distances: OSRM (OpenStreetMap)

## Repository layout

```
freshroute/      planner (feasibility, milp, discount, explain, planner), simulator, generator,
                 market model, validator, baselines, metrics, evaluation runner, what-if, stock
api/             FastAPI service (plan, what-if, simulate, market pulse, stock, eval)
web/             React + TypeScript app (Vite)
config/          all parameters, each with its source
data/processed/  fitted market parameters, festival calendar, OSRM distance matrix
tests/           pytest suite
reports/         evaluation reports (generated)
```

## Licence

MIT, see [LICENSE](LICENSE).
