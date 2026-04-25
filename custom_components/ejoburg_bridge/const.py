"""Constants for e-Joburg Bridge."""

DOMAIN = "ejoburg_bridge"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_BASE_URL = "base_url"
CONF_SCAN_INTERVAL = "scan_interval_minutes"

DEFAULT_BASE_URL = "https://www.e-joburg.org.za"
DEFAULT_SCAN_INTERVAL_MINUTES = 1440
DEFAULT_VAT_RATE_PERCENT = 15.0

TARIFFS_APPROVED_PAGE = "https://joburg.org.za/documents_/Pages/Approved-Tariffs-for-202526-Financial-Year.aspx"
TARIFFS_BOOKLET_FALLBACK_URL = (
    "https://joburg.org.za/documents_/Documents/Tariffs-Booklets.pdf"
)
TARIFFS_CONSOLIDATED_FALLBACK_URL = (
    "https://joburg.org.za/documents_/Documents/Consolidated_Tariffs_2025-26.pdf"
)
TARIFFS_ANNEXURE_FALLBACK_URL = (
    "https://joburg.org.za/documents_/Documents/ITEM_03C_ANNEXURE.pdf"
)

PLATFORMS = ["sensor", "button"]
