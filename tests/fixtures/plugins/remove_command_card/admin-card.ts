export function removeEntry(connection: any, deviceId: string, entryId: string) {
  return connection.sendMessagePromise({
    type: "config/device_registry/remove_config_entry",
    device_id: deviceId,
    config_entry_id: entryId,
  });
}
