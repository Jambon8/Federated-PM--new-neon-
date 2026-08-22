# Dataset Provenance

`data/` is organized by party count. Each `<n>parties/<dataset>/` directory holds
exactly `n` logs, named `party_0` through `party_{n-1}`.

The two-party logs are synthetic federations of public single-organization event
logs: each source log is split into two partitions on a case attribute, so both
partitions describe the same cases from different organizational perspectives.
The three-, four-, and five-party logs derive from the same sources round-robin.
Cite the original archive entry below when reusing a log — not this repository.

## Source archive per dataset

Names and citation keys follow the dataset table in Chapter 6; the keys resolve
in `Thesis/thesis-main/references.bib`.

| Dataset directory | Log | Citation key |
|---|---|---|
| `bpi12` | BPI Challenge 2012 | `bpi2012` |
| `bpi13_closed` | BPI Challenge 2013: Closed Problems | `bpi2013closed` |
| `bpi13_incidents` | BPI Challenge 2013: Incidents | `bpi2013incidents` |
| `bpi13_open` | BPI Challenge 2013: Open Problems | `bpi2013open` |
| `bpi17_offer` | BPI Challenge 2017: Offer Log | `bpi2017offer` |
| `domestic_decl` | BPI Challenge 2020: Domestic Declarations | `bpi2020domestic` |
| `hospital` | Hospital Log (BPI Challenge 2011) | `bpi2011hospital` |
| `international_decl` | BPI Challenge 2020: International Declarations | `bpi2020international` |
| `permit` | BPI Challenge 2020: Travel Permit Data | `bpi2020travel` |
| `requestforpayment` | BPI Challenge 2020: Request for Payment | `bpi2020request` |
| `sepsis` | Sepsis Cases | `mannhardt2016sepsis` |

All entries are published at [4TU.ResearchData](https://data.4tu.nl/).

## Party counts per dataset

| Dataset | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| `bpi13_open`, `bpi13_closed`, `bpi13_incidents`, `sepsis` | yes | yes | yes | yes |
| `bpi12`, `bpi17_offer`, `hospital`, `domestic_decl`, `international_decl`, `permit`, `requestforpayment` | yes | — | — | — |

## The `e4b_*` directories

The controlled scaling study holds the same 500 sampled cases at every party
count, with the row width pinned to 20 events, so that only the party count and
the total row count vary. Directory names carry the cases per party:

- `e4b_<dataset>_c500` at 2, 3, 4, and 5 parties — the scaling series.
- `e4b_<dataset>_c750`, `_c1000`, `_c1250` at two parties — controls whose total
  row count matches the 3-, 4-, and 5-party cells, separating the cost of adding
  a party from the cost of adding rows.

Both series derive from `sepsis` and `bpi13_incidents`.
[e4b_meta.json](e4b_meta.json) records the sampling seed, the trace-length
filter, and the per-cell case and row counts.
Regenerate with `python3 eval/generate_e4b_splits.py`.
