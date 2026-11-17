export function alertLevelLabel(level: string, period: string = "instant") {
  if (level === "critical") return period === "instant" ? "紧急" : "重大";
  return (
    ({ general: "一般", severe: "严重" } as Record<string, string>)[level] ??
    level
  );
}
export const alertUnits: Record<string, string> = {
  device_temperature: "℃",
  device_current: "%额定电流",
  device_leakage: "mA",
  station_utilization: "%",
  station_fault: "%",
  station_offline: "%",
};
