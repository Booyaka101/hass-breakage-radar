export async function primaryEntry(hass, deviceId) {
  const devices = await hass.callWS({ type: "config/device_registry/list" });
  const device = devices.find((d) => d.id === deviceId);
  return device ? device.primary_config_entry : null;
}
export function subentries(device) {
  return device.config_entries_subentries;
}
