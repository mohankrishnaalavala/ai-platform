# P0 costs: idle and per session day, from the billing console

The owner exported the billing console's Reports page (October 2026, cost per day and service) as CSV at 2026-10-24 04:22 UTC: [`billing-report-2026-10-exported-2026-10-24.csv`](billing-report-2026-10-exported-2026-10-24.csv), unchanged. It covers 2026-10-15 to 2026-10-23, the days with any cost.

- **Cost** is the list-price column ("Cost ($)"), before credits. Free-trial credits ("Other savings") covered all of it, so $0.00 was charged.
- **Days** are the billing console's dates. Its figures are rounded to the cent.
- **Project filter:** [to confirm: the export is filtered to this project. The billing account has one other project; if the export wasn't filtered, any cost of that project is included.]
- **Recent days can still grow:** billing data lags, so 2026-10-23 may be incomplete in this export.

## Idle cost

An idle day has no cluster: no Kubernetes Engine and no Compute Engine cost. Two such days are in the report.

| Day | Cost | What it was |
|---|---|---|
| 2026-10-21 | $0.03 | Artifact Registry only |
| 2026-10-22 | $0.03 | Artifact Registry only |

At $0.03 a day, 30 idle days come to $0.90. That's arithmetic on two rounded days, not a month's bill; the export can't show anything below a cent (Cloud Storage, Secret Manager and the rest show $0.00 or nothing).

The SPEC asked for the idle cost after 2 weeks. The project stops on about 2026-10-25, so these 2 idle days out of 9 are what exists.

## Cost per day with a session

| Day | Cost |
|---|---|
| 2026-10-15 | $1.07 |
| 2026-10-16 | $2.92 |
| 2026-10-17 | $3.70 |
| 2026-10-18 | $1.58 |
| 2026-10-19 | $1.60 |
| 2026-10-20 | $7.33 |
| 2026-10-23 | $5.79 (the P4/P5 cluster window, with its Gemini calls: Vertex AI $3.60 that day) |

By service, 2026-10-15 to 2026-10-23: Compute Engine $13.98, Vertex AI $3.61, Networking $3.08, Kubernetes Engine $2.73, Cloud Monitoring $0.51, Artifact Registry $0.14, Cloud Storage $0.00. **Total $24.05** at list price, all covered by credits.

The bill doesn't name sessions, and its days needn't match the UTC dates in the results files, so this table doesn't map days to projects. The 2026-10-23 cluster was created at 14:38 UTC (its session label, `20261023t1438z`, is in the P5 demo records) and was the only one that day.
