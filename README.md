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

### New entities

- **`event.incident`** fires the following event types:
  - `new` / `update`: on websocket messages, with repeated messages that don't change the data skipped. Data: `id`, `body`, `prio`, `type`, `created_at`, `can_respond_until`, `task_ids`, `message_to_speech_url`, `latitude`, `longitude`, `address_type`, `formatted_address`.
  - `response_closed`: once per incident, 10 s after the response deadline (the earliest `can_respond_until` seen, 1:20 after the alert in practice). It runs after a fresh fetch of your response. Data: `id`, `body`, `status`, `acknowledged`, `assigned`, `assigned_skill_ids`, `reported_status`, `available_at_incident_creation`, `on_duty`.
  - `ended`: when the incident gets an `end_time`, or 24 h after creation without one. Data: `id`, `body`, `state`, `end_time`.
- **`binary_sensor.incident_assigned`**: on when your response has assigned skills, and `unknown` without a response. Attributes: `incident_id`, `assigned_skill_ids`, `suggested_skill_ids` (from the incident's skill assignments), `status`, `reported_status`, `available_at_incident_creation` (you were available at the alarm), `on_duty`.
- **`binary_sensor.incident_active`**: on until the incident has ended. Attributes: `incident_id`, `state`.

Existing entities: `sensor.incidents` (body of the last incident, with new `state`, `start_time` and `end_time` attributes), `switch.incident_response` and `binary_sensor.duty`.

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

`sensor.deployment_percentage` estimates the deployment percentage (*uitrukpercentage*) of the [fair deployment proposal](https://help.fireservicerota.com/nl/article/hoe-werkt-het-nieuwe-eerlijk-inzetvoorstel). The API doesn't provide it, so it is calculated from the incident history: (real + virtual deployments) / alarms, over the current calendar year or since the last 10 deployments, whichever is shorter.

- Real deployment: you were assigned, or your reported status is `dispatched`.
- Virtual deployment: you were available (`available_at_incident_creation`) or on duty at the alarm, but rejected or didn't respond, or acknowledged and didn't show up (`no_show`).

Attributes: `alarms`, `real_deployments`, `virtual_deployments`. It is updated after every sync of the incident history.

The incident's `state` (`alerting`, `alerted`, `finished`, `failed`) is about alerting the responders. It is usually `finished` within seconds, while the incident is still ongoing. The incident has ended once it has an `end_time`.

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
