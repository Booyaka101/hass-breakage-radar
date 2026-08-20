class PowerCard extends HTMLElement {
  async loadDevices(hass, entryId) {
    const devices = await hass.callWS({ type: "config/device_registry/list" });
    return devices.filter((d) => d.config_entries.includes(entryId));
  }
}
customElements.define("power-card", PowerCard);
