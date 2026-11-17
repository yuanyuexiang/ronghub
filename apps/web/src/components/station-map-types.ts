import type { MethodResponse } from "openapi-fetch";
import type { typedApi, ApiSchemas } from "../lib/typed-api";
export type MapQueryResponse = MethodResponse<
  typeof typedApi,
  "get",
  "/api/v1/map"
>;
export type StationPoint = ApiSchemas["MapStation"] & { kind?: "station" };
export type MapPoint = MapQueryResponse["items"][number];
export type ClusterPoint = Extract<MapPoint, { kind: "cluster" }>;
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
