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
# then slowly until it is finished.
FAST_POLL_INTERVAL = timedelta(seconds=30)
FAST_POLL_WINDOW = timedelta(minutes=30)
SLOW_POLL_INTERVAL = timedelta(minutes=5)

# An incident that never reports a final state is no longer active after this.
INCIDENT_MAX_AGE = timedelta(hours=24)

# Time after the response deadline before the final assignment is fetched.
RESPONSE_CLOSED_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_RETRY_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_ATTEMPTS = 3

# Incident states after which the incident is no longer active.
ENDED_STATES = {"finished", "failed"}

# Only alerts are incidents to respond to; reports are created afterwards.
INCIDENT_TYPE_ALERT = "incident_alert"
