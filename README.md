# FireServiceRota (custom)

A custom version of the Home Assistant [FireServiceRota / BrandweerRooster](https://www.home-assistant.io/integrations/fireservicerota) integration. It uses the same `fireservicerota` domain and overrides the core integration. Existing config entries, tokens, entities and entity ids keep working, and you don't need to re-authenticate.

Built against Home Assistant 2026.9 and `pyfireservicerota==0.0.49`.

## What changed compared to core

| | Core | This version |
|---|---|---|
| Response after restart | `switch.incident_response` stays `unknown` until the next websocket message | The latest incident and your response are fetched at startup |
| Late assignment changes | Only fetched on websocket messages | The incident is polled every 30 s for 30 minutes, then every 5 minutes while it is active |
| Off duty | Switch is `unavailable` and responding is refused | Switch always shows your response, and responding works whenever an incident is active |
| End of an incident | The last incident stays shown forever | The last incident also stays shown, and `binary_sensor.incident_active` turns off when the incident gets an `end_time` (or 24 h after creation) |
| New vs update | Decided by the library from an in-memory list, so ongoing incidents show up as `new` again after a restart | Decided from the active incident id |
| Assignment after the response deadline | Not available | `event.incident` fires `response_closed` with the final assignment |
| Token refresh failure at setup | Setup fails | Starts a reauth flow |
| Availability | `binary_sensor.duty` only describes an available period, cut off at midnight | `binary_sensor.duty` describes the current period also while unavailable, with its real end and the next period, and `calendar.availability` shows the whole schedule |

### New entities

- **`event.incident`** fires the following event types:
  - `new` / `update`: on websocket messages, with repeated messages that don't change the data skipped. Data: `id`, `body`, `prio`, `type`, `created_at`, `can_respond_until`, `task_ids`, `message_to_speech_url`, `latitude`, `longitude`, `address_type`, `formatted_address`.
  - `response_closed`: once per incident, 10 s after the response deadline (the earliest `can_respond_until` seen, 1:20 after the alert in practice). It runs after a fresh fetch of your response. Data: `id`, `body`, `status`, `acknowledged`, `assigned`, `assigned_skill_ids`, `reported_status`, `available_at_incident_creation`, `on_duty`.
  - `ended`: when the incident gets an `end_time`, or 24 h after creation without one. Data: `id`, `body`, `state`, `end_time`.
- **`binary_sensor.incident_assigned`**: on when your response has assigned skills, and `unknown` without a response. Attributes: `incident_id`, `assigned_skill_ids`, `suggested_skill_ids` (from the incident's skill assignments), `status`, `reported_status`, `available_at_incident_creation` (you were available at the alarm), `on_duty`.
- **`binary_sensor.incident_active`**: on until the incident has ended. Attributes: `incident_id`, `state`.
- **`calendar.availability`**: an `Available` event for each uninterrupted period you are available, also across midnight and across recurring and exception periods. The description lists the schedule types (`recurring`, `exception`, `standby_duty`) and comments. The next 8 days are fetched with every update; other periods are requested from the API when asked for. Check a period before acting on it:

  ```yaml
  - action: calendar.get_events
    target:
      entity_id: calendar.availability
    data:
      start_date_time: "{{ today_at('18:45') }}"
      end_date_time: "{{ today_at('19:00') }}"
    response_variable: schedule
  - condition: template
    value_template: >
      {% set ns = namespace(available=false) %}
      {% for event in schedule['calendar.availability'].events %}
        {% if as_datetime(event.start) <= today_at('18:45')
              and as_datetime(event.end) >= today_at('19:00') %}
          {% set ns.available = true %}
        {% endif %}
      {% endfor %}
      {{ ns.available }}
  ```

Existing entities: `sensor.incidents` (body of the last incident, with new `state`, `start_time` and `end_time` attributes), `switch.incident_response` and `binary_sensor.duty`.

`binary_sensor.duty` is on while you are available. Its attributes describe the current schedule period, available or not: `start_time`, `end_time`, `available`, `active`, `type`, `assigned_function_ids`, `skill_ids` (and `assigned_function`). Periods the API splits at midnight are merged, so `end_time` is the real end. The next period is in `next_start_time`, `next_end_time`, `next_available` and `next_type`. Without a current period only `available: false` and the next period are set.

### Incident statistics

The integration keeps long-term statistics of all your incident alerts. For dashboards it offers the [Statistic](https://www.home-assistant.io/dashboards/statistic/) and [Statistics graph](https://www.home-assistant.io/dashboards/statistics-graph/) cards, `<user id>` being your FireServiceRota user id:

| Statistic id | Value |
|---|---|
| `fireservicerota:user_<user id>_incidents` | Incident alerts you were alerted for (that have a response of yours, so not those from before you joined) |
| `fireservicerota:user_<user id>_acknowledged` | Alerts you acknowledged |
| `fireservicerota:user_<user id>_assigned` | Alerts you were assigned to |
| `fireservicerota:user_<user id>_response_time` | Hours from alert to `end_time` of assigned incidents (at most 24 h each) |

On first start the integration fetches all incidents the API returns. That takes one request per incident, one every 4 seconds. When the API refuses a request (it rate limits after about 20 requests within a minute), the integration waits a minute and tries again, and otherwise continues on the next sync. After that it checks for new incidents at startup, daily and after every `ended` event. An incident without an `end_time` is fetched again until it has one, or until it is 7 days old. Incidents are counted in the hour they were created.

### Deployment percentage

`sensor.deployment_percentage_day` and `sensor.deployment_percentage_evening_night_weekend` estimate the deployment percentage (*uitrukpercentage*) of the [fair deployment proposal](https://help.fireservicerota.com/nl/article/hoe-werkt-het-nieuwe-eerlijk-inzetvoorstel). There is one per duty schedule, because the schedules have independent percentages:

- Day: weekdays from 6:00 until 18:00.
- Evening, night and weekend: all other times.

An incident counts for the schedule it started in. The API doesn't provide the percentage, so it is calculated from the incident history: (real + virtual deployments) / alarms, over the current calendar year or since the last 10 deployments, whichever is shorter.

- Real deployment: you were assigned, or your reported status is `dispatched`.
- Virtual deployment: you were available (`available_at_incident_creation`) or on duty at the alarm, but rejected or didn't respond, or acknowledged and didn't show up (`no_show`).

Attributes: `alarms`, `real_deployments`, `virtual_deployments`. It is updated after every sync of the incident history.

#### Colleagues and the seat ranking

Select colleagues you compete with for a seat (for example other aspirants) under the integration's **Configure**. They are listed from the responses in your incident history. For each colleague you get the same two sensors, for example `sensor.deployment_percentage_mark_day`.

`sensor.aspirant_ranking` ranks you and the colleagues during an active incident, like the fair deployment proposal:

1. Those who acknowledged, then those who can still respond. Rejected or expired responses are left out.
2. Priority group at the alarm: on duty (`on_duty`), available in a paraat team, available, not available.
3. Lowest deployment percentage of the incident's schedule.

Its state is your position among those who acknowledged, so `1` means you would get the seat. Attributes: `candidates` (`name`, `status`, `group`, `percentage` in rank order), `schedule`, `incident_id`. The proposal fills seats with percentages per function, which the API doesn't provide, so this is an estimate. The actual assignment shows in `binary_sensor.incident_assigned`.

The incident's `state` (`alerting`, `alerted`, `finished`, `failed`) is about alerting the responders. It is usually `finished` within seconds, while the incident is still ongoing. The incident has ended once it has an `end_time`.

### Actions

- **`fireservicerota.set_availability`** sets your availability for a period, like a schedule exception in the app. Fields:
  - `end_time` (required) and `start_time` (defaults to now).
  - `availability` (required): `available`, `unavailable`, or `clear` to remove exceptions in the period so your regular schedule applies again.
  - `comments` (optional).
  - `config_entry_id`, only needed with more than one account.

  The exception is saved even if it causes schedule warnings, and its source is shown as geofencing. `binary_sensor.duty` is refreshed afterwards.

  ```yaml
  action: fireservicerota.set_availability
  data:
    end_time: "{{ now() + timedelta(minutes=15) }}"
    availability: unavailable
  ```

## Installation

### HACS

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/colinpieper/fireservicerota-custom` with type *Integration*.
2. Download *FireServiceRota (custom)*.
3. Restart Home Assistant. The log warns that a custom integration overrides `fireservicerota`, which is expected.

### Manual

Copy `custom_components/fireservicerota` to `/config/custom_components/fireservicerota` and restart Home Assistant.

## Rollback

Remove the integration in HACS (or delete `/config/custom_components/fireservicerota`) and restart. The core integration takes over the existing config entry and entities. `event.incident`, `binary_sensor.incident_assigned` and `binary_sensor.incident_active` become unavailable, and you can delete them.

## Example automations

Notify on a new incident:

```yaml
triggers:
  - trigger: state
    entity_id: event.incident
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.incident
    attribute: event_type
    state: new
actions:
  - action: notify.send_message
    target:
      entity_id: notify.my_phone
    data:
      title: "🚒 New incident"
      message: "{{ state_attr('event.incident', 'body') }}"
```

Notify whether you were assigned after acknowledging:

```yaml
triggers:
  - trigger: state
    entity_id: event.incident
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.incident
    attribute: event_type
    state: response_closed
  - condition: state
    entity_id: event.incident
    attribute: acknowledged
    state: true
actions:
  - if:
      - condition: state
        entity_id: event.incident
        attribute: assigned
        state: true
    then:
      - action: notify.send_message
        target:
          entity_id: notify.my_phone
        data:
          title: "Assigned"
          message: "You are assigned to {{ state_attr('event.incident', 'body') }}"
    else:
      - action: notify.send_message
        target:
          entity_id: notify.my_phone
        data:
          title: "Not assigned"
          message: "You are not assigned to {{ state_attr('event.incident', 'body') }}"
```

## Development

```sh
uv sync --group test --python 3.14
uv run pytest
```

The first commit is the unmodified core integration, so each later change can be turned into an upstream pull request on its own.
