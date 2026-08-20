import { HomeAssistant } from "custom-card-helpers";

export async function primaryEntry(hass: HomeAssistant, deviceId: string) {
  const devices = await hass.callWS<any[]>({ type: "config/device_registry/list" });
  const device = devices.find((d) => d.id === deviceId);
  return device?.primary_config_entry ?? null;
}

export function subentries(device: any) {
  return device.config_entries_subentries;
}
