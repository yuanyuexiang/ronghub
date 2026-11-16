export type StationPoint = {
  kind?: "station";
  id: number;
  name: string;
  district: string;
  operator_name: string;
  operator_id: number;
  address: string;
  status: string;
  station_type: string;
  usage_type: string;
  construction_site: string;
  site_state: string;
  source_sync_state: string;
  source_first_connected_at: string | null;
  power_kw: string;
  connectors: number;
  observed_connectors: number;
  missing_connector_snapshots: number;
  runtime_state: string;
  effective_stars: number | null;
  longitude: string | null;
  latitude: string | null;
  coordinate_system: string;
  location_is_demo: boolean;
  unplottable_reason: string | null;
  synced_at: string;
};
export type ClusterPoint = {
  kind: "cluster";
  key: string;
  count: number;
  longitude: string;
  latitude: string;
  bounds: [number, number, number, number];
};
export type MapPoint = StationPoint | ClusterPoint;
export type Viewport = {
  west: number;
  south: number;
  east: number;
  north: number;
  zoom: number;
};
export const businessStates: Record<string, string> = {
  pending: "待接入",
  access_approved: "接入通过",
  accepted: "验收通过",
  operating: "运营中",
  decommissioned: "已核销",
};
export const runtimeStates: Record<string, string> = {
  available: "有空闲接口",
  charging: "充电中",
  occupied: "接口占用",
  offline: "全部快照离线",
  fault: "有故障接口",
  unknown: "快照不足 / 未知",
};
export const stationTypes: Record<string, string> = {
  charging: "充电站",
  swapping: "换电站",
  mixed: "充换电混合",
  unknown: "类型未提供",
};
export const locationReasons: Record<string, string> = {
  missing_coordinates: "来源未提供坐标",
  unsupported_coordinate_system: "坐标系尚未适配",
  outside_projection: "超出当前投影视图范围",
};
