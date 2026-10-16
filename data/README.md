# Data

`data/raw/` and `data/warehouse.duckdb` are git-ignored and rebuilt by `python -m copilot_audit setup`.

**Source.** Brazilian E-Commerce Public Dataset by Olist (Kaggle, `olistbr/brazilian-ecommerce`), CC BY-NC-SA 4.0.
Real, anonymised marketplace orders placed 2016-2018. The dataset is not redistributed here.
`make data` fetches the same nine files from a public GitHub mirror; the official Kaggle download works identically
(the loader accepts both the Kaggle ISO timestamps and the `dd-mm-yyyy HH:MM` format used by some mirrors).

**Overlay.** `trap_manifest.json` records the deterministic (seeded) overlays added on top of the real data.
See the README section "Where the traps come from".
