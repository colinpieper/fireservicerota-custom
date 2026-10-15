"""Constants for the FireServiceRota integration."""

from datetime import timedelta

DOMAIN = "fireservicerota"

URL_LIST = {
    "www.brandweerrooster.nl": "BrandweerRooster",
    "www.fireservicerota.co.uk": "FireServiceRota",
}
WSS_BWRURL = "wss://{0}/cable?access_token={1}"

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"

# Dispatcher signals, formatted with the config entry id.
SIGNAL_UPDATE = f"{DOMAIN}_{{}}_update"
SIGNAL_EVENT = f"{DOMAIN}_{{}}_event"

# Incident event types.
EVENT_NEW = "new"
EVENT_UPDATE = "update"
EVENT_RESPONSE_CLOSED = "response_closed"
EVENT_ENDED = "ended"

# Poll the active incident often while responses and assignments settle,
# then slowly until it is no longer active.
FAST_POLL_INTERVAL = timedelta(seconds=30)
FAST_POLL_WINDOW = timedelta(minutes=30)
SLOW_POLL_INTERVAL = timedelta(minutes=5)

# The API does not report when an incident is over, so an incident is active
# for a fixed time after it was created. It stays shown as the last incident.
INCIDENT_ACTIVE_DURATION = timedelta(hours=2)

# How far back the latest incident is looked up at startup.
INCIDENT_LOOKUP_WINDOW = timedelta(hours=24)

# Time after the response deadline before the final assignment is fetched.
RESPONSE_CLOSED_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_RETRY_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_ATTEMPTS = 3

# Only alerts are incidents to respond to; reports are created afterwards.
INCIDENT_TYPE_ALERT = "incident_alert"
