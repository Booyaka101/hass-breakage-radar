// config_entries used to be a list; we now resolve entries another way.
/* The old flow read primary_config_entry off every device returned by
   hass.callWS({ type: "config/device_registry/list" }). */
class CommentOnlyCard extends HTMLElement {
  render(hass) {
    return hass.callWS({ type: "config/entity_registry/list" });
  }
}
