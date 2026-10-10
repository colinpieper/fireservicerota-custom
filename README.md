# FireServiceRota (custom)

A custom version of the Home Assistant [FireServiceRota / BrandweerRooster](https://www.home-assistant.io/integrations/fireservicerota) integration. It uses the same `fireservicerota` domain and overrides the core integration. Existing config entries, tokens, entities and entity ids keep working, and you don't need to re-authenticate.

Built against Home Assistant 2026.9 and `pyfireservicerota==0.0.49`.

## What changed compared to core

| | Core | This version |
|---|---|---|
| Response after restart | `switch.incident_response` stays `unknown` until the next websocket message | The latest incident and your response are fetched at startup |
| Late assignment changes | Only fetched on websocket messages | The incident is polled every 30 s for 30 minutes, then every 5 minutes until it is finished |
| Off duty | Switch is `unavailable` and responding is refused | Switch always shows your response, and responding works whenever an incident is active |
| End of an incident | The last incident stays shown forever | The incident ends when its state is `finished`/`failed` (or 24 h after creation), and the incident entities become `unknown` |
| New vs update | Decided by the library from an in-memory list, so ongoing incidents show up as `new` again after a restart | Decided from the active incident id |
| Assignment after the response deadline | Not available | `event.incident` fires `response_closed` with the final assignment |
| Token refresh failure at setup | Setup fails | Starts a reauth flow |

### New entities

- **`event.incident`** fires the following event types:
  - `new` / `update`: on websocket messages. Data: `id`, `body`, `prio`, `type`, `created_at`, `can_respond_until`, `task_ids`, `message_to_speech_url`, `latitude`, `longitude`, `address_type`, `formatted_address`.
  - `response_closed`: once per incident, 10 s after the response deadline (the earliest `can_respond_until` seen, 1:20 after the alert in practice). It runs after a fresh fetch of your response. Data: `id`, `body`, `status`, `acknowledged`, `assigned`, `assigned_skill_ids`, `reported_status`.
  - `ended`: when the incident is finished, failed or older than 24 hours. Data: `id`, `body`, `state`.
- **`binary_sensor.incident_assigned`**: on when your response has assigned skills, and `unknown` without a response. Attributes: `incident_id`, `assigned_skill_ids`, `suggested_skill_ids` (from the incident's skill assignments), `status`, `reported_status`.
- **`binary_sensor.incident_active`**: on while an incident is active. Attributes: `incident_id`, `state`.

Existing entities: `sensor.incidents` (incident body, with a new `state` attribute), `switch.incident_response` and `binary_sensor.duty`.

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
