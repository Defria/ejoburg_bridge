# e-Joburg Bridge (Home Assistant custom integration)

This integration logs in to e-Joburg, fetches account pages, and exposes key data as sensors.

## Features in v0.1.0

- JSF login to `https://www.e-joburg.org.za`
- Account overview page scrape (`/account-manager`)
- Payment history page scrape (`/payment-history`)
- Statement history parsing (`/statement-history`)
- Latest statement PDF download and local cache

## Install

Copy this folder into your HA config directory:

- `custom_components/ejoburg_bridge`

Example destination:

- `/config/custom_components/ejoburg_bridge`

## Add integration

1. Restart Home Assistant.
2. Go to **Settings -> Devices & Services -> Add Integration**.
3. Search for **e-Joburg Bridge**.
4. Enter:
   - Username (example: `john.doe@example.com`)
   - Password
   - Municipal account number (example: `12345`)
   - Base URL (default already set)
   - Refresh interval (minutes)

Refresh interval range:

- Minimum: `1440` minutes (1 day)
- Maximum: `44640` minutes (31 days)

## Entities

- `sensor.e_joburg_latest_statement_amount`
- `sensor.e_joburg_statement_row_count`
- `sensor.e_joburg_latest_statement_pdf_url`
- `sensor.e_joburg_account_number_detected`
- `sensor.e_joburg_tariffs_status`
- `button.e_joburg_refresh`
- `button.e_joburg_refresh_tariffs`
- `button.e_joburg_open_latest_statement`

If Home Assistant already has older entity IDs from earlier builds,
remove and re-add the integration once to apply the namespaced IDs cleanly.

## Local PDF cache

Latest statement PDF is cached in Home Assistant under:

- `www/ejoburg_bridge/<entry_id>/latest_<account>.pdf`

URL exposed:

- `/local/ejoburg_bridge/<entry_id>/latest_<account>.pdf`

## Date format

- Dashboard and parsed statement dates are shown in `YYYY/MM/DD` format.
- Date fields may show `-` when the source statement does not include a parseable date.

## Service

- `ejoburg_bridge.refresh`
- `ejoburg_bridge.refresh_tariffs`

Optional field:

- `entry_id`

## Tariffs (download-once + manual refresh)

- Tariff data is downloaded once during first successful setup/refresh and cached locally.
- No periodic polling is used for tariff schedules.
- You can refresh tariffs manually via:
  - button: `button.e_joburg_refresh_tariffs`
  - service: `ejoburg_bridge.refresh_tariffs`
- Runtime local CSV cache is exposed at:
  - `/local/ejoburg_bridge/<entry_id>/tariffs_latest.csv`
- Prepaid tariff rows are parsed from the tariff booklet PDF.
- Postpaid/conventional tariff rows are parsed from the approved annexure PDF (`ITEM_03C_ANNEXURE.pdf`).

## Disclaimer

This integration is a hobby project and is provided as-is.

It is not affiliated with, endorsed by, or sponsored by the City of Johannesburg.

"City of Johannesburg", "e-Joburg", and any related names/logos are the property
of their respective owners. All rights to third-party marks and branding remain
with those owners.

Use the official e-Joburg portal for authoritative account management and records:

- https://www.e-joburg.org.za/

Use this integration at your own discretion and risk.
