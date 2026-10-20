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
SIGNAL_HISTORY = f"{DOMAIN}_{{}}_history"

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

# An incident is active until it has an end time. One that never gets an end
# time is no longer active after this. It stays shown as the last incident.
INCIDENT_MAX_AGE = timedelta(hours=24)

# Time after the response deadline before the final assignment is fetched.
RESPONSE_CLOSED_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_RETRY_DELAY = timedelta(seconds=10)
RESPONSE_CLOSED_ATTEMPTS = 3

# Only alerts are incidents to respond to; reports are created afterwards.
INCIDENT_TYPE_ALERT = "incident_alert"

# Actions.
SERVICE_SET_AVAILABILITY = "set_availability"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_AVAILABILITY = "availability"
ATTR_COMMENTS = "comments"
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_CLEAR = "clear"

# Incident history statistics.
HISTORY_PER_PAGE = 100
# The API refuses requests after about 20 within a minute.
HISTORY_REQUEST_DELAY = 4
HISTORY_RETRY_DELAY = 60
HISTORY_SYNC_INTERVAL = timedelta(days=1)
# An incident is no longer fetched again after this, or once its report is
# approved, unless the API lists it as updated since the previous sync.
HISTORY_FINAL_AGE = timedelta(days=30)
# Margin on the previous sync time when asking for updated incidents.
HISTORY_UPDATED_MARGIN = timedelta(minutes=10)
MAX_RESPONSE_MINUTES = 1440
# The deployment percentage covers at most this many recent deployments.
DEPLOYMENT_WINDOW = 10

# Duty schedules with their own deployment percentages: weekdays from
# DAY_SCHEDULE_START until DAY_SCHEDULE_END, and all other times.
SCHEDULE_DAY = "day"
SCHEDULE_EVENING_NIGHT_WEEKEND = "evening_night_weekend"
SCHEDULES = (SCHEDULE_DAY, SCHEDULE_EVENING_NIGHT_WEEKEND)
DAY_SCHEDULE_START = 6
DAY_SCHEDULE_END = 18

# Options.
CONF_COLLEAGUES = "colleagues"
