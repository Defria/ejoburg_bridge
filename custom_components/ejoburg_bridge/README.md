# e-Joburg Bridge (Home Assistant custom integration)

This integration retrieves CoJ account and statement data and exposes key data as sensors.

It supports three data sources:

- `auto` (default): tries the CoJ App first, and falls back to the portal when
  the CoJ App is not available for the account. The last working backend is
  remembered per entry.
- `portal`: existing JSF portal integration for accounts that only use
  `https://www.e-joburg.org.za`.
- CoJ App: first-party CoJ CSD App, with no account-page scraping.

### Same password, CoJ App username

The **password** is shared between the e-Joburg web portal and the CoJ App.
However, the CoJ App login **username** is the **email address or mobile
number registered on the CoJ App** — not the e-Joburg portal username.

> **How to find your CoJ App username**
>
> 1. Log in to the e-Joburg portal: `https://www.e-joburg.org.za`
> 2. Go to **Manage Personal Information** (`/manage-personal-information`)
> 3. Use the **email address** (or the **mobile number**) shown on that page as
>    the username in this integration.
>
> Your account must also be **registered on the CoJ App**. An account that only
> exists on the e-Joburg portal (for example a test account whose portal email
> returns `User does not exist`) will transparently fall back to the `portal`
> backend in `auto` mode. To enable the CoJ App for such an account, sign in
> once via the official CoJ App (which links the identity through its OTP
> `sign up` flow), then the same email/mobile will work here.

## Features in v0.1.0

- JSF login to `https://www.e-joburg.org.za`
- Account overview page scrape (`/account-manager`)
- Payment history page scrape (`/payment-history`)
- Statement history parsing (`/statement-history`)
- Latest statement PDF download and local cache

## CoJ App backend

Select **CoJ App** (or **Automatic**) during setup to use these first-party
endpoints:

- `POST /auth/auth-service-prod/auth/v1` for the application token
- `POST /customers/customer-service-prod/sign-in/v2` for user authentication
- `POST /bills/bill-service-prod/invoices/v1` for statement metadata
- `GET /bills/bill-service-prod/download-bill/v1` for a signed PDF URL

The integration uses your existing e-Joburg **password**, with the **email or
mobile number** from your portal profile as the CoJ App username. The account
must be registered on the CoJ App (see "How to find your CoJ App username"
above).

To reach the CoJ App backend, set the **CoJ App client credential** option to
the app-level client credential that the official CoJ mobile app embeds
(the same value is used by every install of the app to obtain an API token
before login). Put it in `secrets.yaml` and reference it, or type it in during
setup. Leave this field **blank** to disable the CoJ App backend — in
`auto` mode the integration will then use the portal only.

In CoJ App and Automatic (when the CoJ App is active) mode, account and
statement data comes only from JSON APIs and tariffs come from the local
cache/bundled CSV. No portal, tariff-page, or PDF content is parsed. When
Automatic falls back to the `portal` backend, the portal's existing scraping
behavior is used so portal-only accounts keep working.

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

## Private document cache

Statement PDFs are cached privately in Home Assistant under:

- `ejoburg_bridge/<entry_id>/statements/`

The dashboard receives an authenticated Home Assistant API URL:

- `/api/ejoburg_bridge/documents/<entry_id>/<document_id>`

Unlike `/local`, this route requires Home Assistant authentication and works
when a fresh installation did not have a `www` directory at startup.

After upgrading, old integration-generated files under
`www/ejoburg_bridge/` are no longer used. They may be removed manually after
confirming the new statement links work.

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
- Runtime CSV cache is exposed through the authenticated document route:
  - `/api/ejoburg_bridge/documents/<entry_id>/tariffs.csv`
- Prepaid tariff rows are parsed from the tariff booklet PDF.
- Postpaid/conventional tariff rows are parsed from the approved annexure PDF (`ITEM_03C_ANNEXURE.pdf`).

## Acknowledgements

Special thanks to **Christoff Jacobs, PhD** ([@toffiecj](https://github.com/toffiecj) · [LinkedIn](https://www.linkedin.com/in/christoff-jacobs-phd-ab22916/)) for his contribution and testing — without him, this project would not have got off the ground.

Thanks also to **Justin Porteous** ([@jgporteous](https://github.com/jgporteous) · [LinkedIn](https://www.linkedin.com/in/justin-porteous-90b2b615/)) for testing the alpha release.

## Disclaimer

This integration is a hobby project and is provided as-is.

It is not affiliated with, endorsed by, or sponsored by the City of Johannesburg.

"City of Johannesburg", "e-Joburg", and any related names/logos are the property
of their respective owners. All rights to third-party marks and branding remain
with those owners.

Use the official e-Joburg portal for authoritative account management and records:

- https://www.e-joburg.org.za/

Use this integration at your own discretion and risk.
