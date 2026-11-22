"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { StationObservations } from "./station-observations";
import { DeviceInventory } from "./device-inventory";
import { businessStates, stationTypes } from "./station-map-types";

type Detail = ApiSchemas["StationDetail"];
const labels: Record<string, string> = {
  direct: "直供电",
  transferred: "转供电",
  commercial: "商业用电",
  general_industrial: "普通工业用电",
  large_industrial: "大工业用电",
  other: "其他",
  free: "免费",
  paid: "收费",
  limited_free: "限时免费停车",
  charging_discount: "充电限时减免",
  onsite: "参考场地实际收费标准",
  public: "公共",
  personal: "个人",
  bus: "公交专用",
  sanitation: "环卫专用",
  logistics: "物流专用",
  taxi: "出租车专用",
  car_sharing: "分时租赁专用",
  community_shared: "小区共享",
  workplace: "单位专用",
  private_shared: "私人共享桩",
  public_institution: "公共机构",
  residential: "居民区",
  enterprise: "企事业单位",
  office: "写字楼",
  industrial: "工业园区",
  transit: "交通枢纽",
  culture_sports: "大型文体设施",
  highway: "城际高速服务区",
  green_space: "城市绿地",
  roadside: "路边停车位",
  building_parking: "大型建筑配建停车场",
  scenic_area: "风景区",
  bus_depot: "公交场站",
  fuel_station: "加油加气站",
  taxi_site: "出租车场所",
  constructing: "建设中",
  offline: "关闭下线",
  maintenance: "维护中",
  normal: "正常使用",
  active: "正常同步",
  removed: "来源已下架",
};
const display = (value: string | null | undefined) =>
  !value || value === "unknown" ? "来源未提供" : labels[value] || value;
const timestamp = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "来源未提供";
function Item({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div>
      <dt>{name}</dt>
      <dd>{children}</dd>
    </div>
  );
}
export function StationDetail({
  stationId,
  onClose,
}: {
  stationId: number;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [station, setStation] = useState<Detail | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    dialog.current?.showModal();
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setStation(null);
    typedApi
      .GET("/api/v1/stations/{station_id}", {
        params: { path: { station_id: stationId } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "场站详情读取失败"));
        setStation(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "场站详情读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [stationId]);
  const [tab, setTab] = useState("basic");
  const canHistory = useCapability("statistics.read"),
    canFlow = useCapability("applications.read");
  const [operatorName, setOperatorName] = useState<string | null>(null);
  useEffect(() => {
    if (!station) return;
    const controller = new AbortController();
    setOperatorName(null);
    typedApi
      .GET("/api/v1/operators", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data }) => {
        if (!controller.signal.aborted)
          setOperatorName(
            data?.items.find((item) => item.id === station.operator_id)?.name ??
              null,
          );
      })
      .catch(() => {
        if (!controller.signal.aborted) setOperatorName(null);
      });
    return () => controller.abort();
  }, [station?.operator_id]);
  const f = station?.facilities;
  return (
    <dialog
      ref={dialog}
      className="station-dialog station-detail-workspace"
      onCancel={onClose}
      aria-labelledby="station-detail-title"
    >
      <div className="panel-title">
        <div>
          <h2 id="station-detail-title">{station?.name || "场站与设备详情"}</h2>
          <p>{station?.address || "正在读取场站信息"}</p>
        </div>
        <button className="outline" onClick={onClose}>
          关闭详情
        </button>
      </div>
      {loading ? (
        <p className="empty" role="status">
          正在加载场站与设备…
        </p>
      ) : error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : (
        station &&
        f && (
          <div className="station-detail-body">
            <div className="station-detail-summary">
              <span className="tag">
                {businessStates[station.status] || station.status}
              </span>
              <span>
                {stationTypes[station.station_type] || station.station_type}
              </span>
              <span>台账额定总功率 {station.power_kw} kW</span>
            </div>
            <nav className="record-tabs" aria-label="场站详情分区">
              {[
                ["basic", "基本信息"],
                ["devices", "设备信息"],
                ...(canHistory || canFlow
                  ? [["observations", "历史与流转"]]
                  : []),
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={tab === key}
                  onClick={() => setTab(key)}
                >
                  {label}
                </button>
              ))}
            </nav>
            <div
              className="record-pane station-basic-sections"
              hidden={tab !== "basic"}
            >
              <section aria-label="场站基本资料">
                <h3>基本资料</h3>
                <dl className="station-facts">
                  <Item name="所属运营商">
                    {operatorName ?? "未读取到名称"}
                  </Item>
                  <Item name="来源场站编码">{station.external_id}</Item>
                  <Item name="来源 / 同步时间">
                    {station.source} · {timestamp(station.synced_at)}
                    （北京时间）
                  </Item>
                  <Item name="所在区域">{station.district}</Item>
                  <Item name="全省唯一备案号">{display(f.filing_code)}</Item>
                  <Item name="用途">{display(station.usage_type)}</Item>
                  <Item name="建设场所">
                    {display(station.construction_site)}
                  </Item>
                  <Item name="来源站点状态">{display(station.site_state)}</Item>
                  <Item name="来源接入状态">
                    {display(station.source_sync_state)}
                  </Item>
                  <Item name="经纬度 / 坐标系">
                    {station.longitude == null || station.latitude == null
                      ? "来源未提供"
                      : `${station.longitude}, ${station.latitude} / ${station.coordinate_system.toUpperCase()}${station.location_is_demo ? "（演示坐标）" : ""}`}
                  </Item>
                  <Item name="来源首次上报时间">
                    {timestamp(station.source_first_reported_at)}
                  </Item>
                  <Item name="来源首次接入时间">
                    {timestamp(station.source_first_connected_at)}
                  </Item>
                </dl>
              </section>
              <section aria-label="场站供电配套">
                <h3>供电配套</h3>
                <dl className="station-facts">
                  <Item name="供电类型">{display(f.supply_type)}</Item>
                  <Item name="电费类型">{display(f.electricity_tariff)}</Item>
                  <Item name="报装电源容量">
                    {f.supply_capacity == null
                      ? "来源未提供"
                      : `${f.supply_capacity} ${f.supply_capacity_unit === "unknown" ? "（来源未注明单位）" : f.supply_capacity_unit}`}
                  </Item>
                  <Item name="是否独立报装">
                    {f.independent_connection == null
                      ? "来源未提供"
                      : f.independent_connection
                        ? "是"
                        : "否"}
                  </Item>
                </dl>
              </section>
              <section aria-label="场站运营与停车">
                <h3>运营与停车</h3>
                <dl className="station-facts">
                  <Item name="正式投运日期（来源）">
                    {display(f.commissioned_on)}
                  </Item>
                  <Item name="营业时间">{display(f.business_hours)}</Item>
                  <Item name="车位数量">
                    {f.parking_spaces == null
                      ? "来源未提供"
                      : `${f.parking_spaces} 个`}
                  </Item>
                  <Item name="停车费类型">{display(f.parking_fee_type)}</Item>
                  <Item name="停车费描述">
                    {display(f.parking_fee_description)}
                  </Item>
                </dl>
              </section>
              <section aria-label="场站联系方式">
                <h3>联系方式</h3>
                {station.contact_accessible ? (
                  <dl className="station-facts">
                    <Item name="联系人">{display(station.contact?.name)}</Item>
                    <Item name="联系电话">
                      {display(station.contact?.phone)}
                    </Item>
                  </dl>
                ) : (
                  <p className="muted">当前岗位未授权查看场站联系人。</p>
                )}
              </section>
            </div>
            <div className="record-pane" hidden={tab !== "observations"}>
              <StationObservations
                key={`observations-${stationId}`}
                stationId={stationId}
              />
            </div>
            <div className="record-pane" hidden={tab !== "devices"}>
              <DeviceInventory key={stationId} stationId={stationId} />
            </div>
          </div>
        )
      )}
    </dialog>
  );
}
