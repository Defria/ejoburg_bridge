# City of Johannesburg Prepaid Electricity Tariffs (FY2025/26)

This note summarizes prepaid electricity tariff information published by the City of Johannesburg for FY2025/26.

Use this as a quick reference only. For billing, disputes, or legal/official use, always verify against the official published tariff documents.

## Official Sources

- Tariffs landing page: `https://joburg.org.za/documents_/Pages/Key%20Documents/other/links/tariffs/Tariffs.aspx`
- Approved tariffs page (FY2025/26): `https://joburg.org.za/documents_/Pages/Approved-Tariffs-for-202526-Financial-Year.aspx`
- Consolidated tariff PDF: `https://joburg.org.za/documents_/Documents/Consolidated_Tariffs_2025-26.pdf`
- Tariffs booklet PDF: `https://joburg.org.za/documents_/Documents/Tariffs-Booklets.pdf`

## Prepaid Variants (Residential)

- `Residential Prepaid Low (Indigent)`
  - Targeted at qualifying indigent customers.
  - Uses an inclining block tariff structure.
  - Explanatory text indicates customers are cushioned from service and capacity charges.
- `Residential Prepaid High`
  - For other residential prepaid customers.
  - Uses an inclining block tariff structure.
  - Explanatory text indicates fixed monthly charges apply.

## FY2025/26 Block Rates (c/kWh)

VAT treatment used in this document/data:

- Rates are captured as `ex VAT`.
- Basis: the City tariff booklet uses wording such as "charges are exclusive of VAT" / "prices illustrated exclude VAT".
- If the City publishes a tariff item as VAT-inclusive in a future update, that item should be flagged explicitly in the dataset.

### Residential Prepaid Low (Indigent)

| Block | Usage range (kWh/month) | Rate (c/kWh) | Rate (R/kWh) |
|---|---:|---:|---:|
| Block 1 | 0-350 | 249.86 | 2.4986 |
| Block 2 | >350-500 | 305.64 | 3.0564 |
| Block 3 | >500 | 370.42 | 3.7042 |

Fixed monthly charges (from explanatory booklet text):

- Service charge: `R0.00`
- Capacity charge: `R0.00`

### Residential Prepaid High

| Block | Usage range (kWh/month) | Rate (c/kWh) | Rate (R/kWh) |
|---|---:|---:|---:|
| Block 1 | 0-350 | 266.45 | 2.6645 |
| Block 2 | >350-500 | 305.64 | 3.0564 |
| Block 3 | >500 | 348.26 | 3.4826 |

Fixed monthly charges (from explanatory booklet text):

- Service charge: `R70.00`
- Capacity charge: `R130.00`

## Effective Date

- The FY2025/26 tariff communication indicates application from `2025-07-01`.

## Data File

- Machine-readable CSV: `data/coj_prepaid_electricity_tariffs_2025_26.csv`

## Disclaimer

- This repository is not an official City publication.
- Tariffs can be amended; always verify current values on official City pages and PDFs.
