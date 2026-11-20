"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { businessStates, stationTypes } from "./station-map-types";
type Profile = ApiSchemas["SourceProfile"];
type Content = ApiSchemas["ReviewContent"];
export type ApplicationReviewState = {
  applicationId: number;
  version: number;
  revision: number;
  token: string | null;
  busy: boolean;
  categories: Record<string, string>;
};
const unknown = (value: string | null | undefined) => value ?? "未提供";
const clock = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未提供";
const labels: Record<string, string> = {
  direct: "直供电",
  transferred: "转供电",
  commercial: "商业用电",
  general_industrial: "普通工业用电",
  large_industrial: "大工业用电",
  other: "其他",
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
  free: "免费",
  paid: "收费",
  limited_free: "限时免费停车",
  charging_discount: "充电限时减免",
  onsite: "参考场地实际收费标准",
  unknown: "未提供",
};
const label = (value: string | null) =>
  value == null ? "未提供" : labels[value] || value;
function SourceInfo({ value }: { value: Profile | null }) {
  if (!value) return <p className="muted">当前岗位未获场站资料读取权限。</p>;
  const f = value.facilities;
  const rows: [string, string][] = [
    ["场站名称", value.station_name],
    ["运营商", value.operator_name],
    ["来源场站编码", value.external_id],
    ["来源", value.source],
    ["地址", value.address],
    ["区域", value.district],
    ["场站种类", stationTypes[value.station_type] || value.station_type],
    ["用途", label(value.usage_type)],
    ["建设场所", label(value.construction_site)],
    [
      "平台状态",
      businessStates[value.platform_status] || value.platform_status,
    ],
    ["站点额定总功率", `${value.power_kw} kW`],
    ["台账接口数", String(value.connectors)],
    ["供电类型", label(f.supply_type)],
    ["电费类型", label(f.electricity_tariff)],
    [
      "报装电源容量",
      f.supply_capacity == null
        ? "未提供"
        : `${f.supply_capacity} ${f.supply_capacity_unit === "unknown" ? "（来源未注明单位）" : f.supply_capacity_unit}`,
    ],
    [
      "是否独立报装",
      f.independent_connection == null
        ? "未提供"
        : f.independent_connection
          ? "是"
          : "否",
    ],
    [
      "车位数量",
      f.parking_spaces == null ? "未提供" : `${f.parking_spaces} 个`,
    ],
    ["正式投运日期（来源）", unknown(f.commissioned_on)],
    ["停车费类型", label(f.parking_fee_type)],
    ["停车说明", unknown(f.parking_fee_description)],
    ["营业时间", unknown(f.business_hours)],
    ["全省备案号", unknown(f.filing_code)],
    [
      "经纬度",
      value.longitude == null || value.latitude == null
        ? "未提供"
        : `${value.longitude}, ${value.latitude} / ${value.coordinate_system}${value.location_is_demo ? "（演示）" : ""}`,
    ],
    ["来源最近同步", clock(value.source_synced_at)],
  ];
  return (
    <dl className="station-facts application-source-profile">
      {rows.map(([name, text]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{text}</dd>
        </div>
      ))}
    </dl>
  );
}
function SubmittedContent({ value }: { value: Content }) {
  const raw = value.payload.contact;
  const contact =
    raw && typeof raw === "object" && !Array.isArray(raw) ? raw : null;
  return (
    <>
      <SourceInfo value={value.source_profile} />
      {value.source_profile && (
        <section className="materials" aria-label="送审设备明细">
          <h4>设备与枪口明细</h4>
          {value.equipment == null ? (
            <p className="muted">
              此历史版本未归档设备明细，不能用当前台账补作历史证明。
            </p>
          ) : (
            <>
              <p className="muted">
                归档设备身份与规格，不将运行中的实时状态作为审核依据。共
                {value.equipment.length}台设备。
              </p>
              {value.equipment.map((equipment) => (
                <details key={equipment.id}>
                  <summary>
                    {equipment.name} · {equipment.external_id} ·{" "}
                    {equipment.power_kw} kW · {equipment.connectors.length}
                    个枪口
                  </summary>
                  <dl className="station-facts">
                    <div>
                      <dt>型号</dt>
                      <dd>{equipment.model_name ?? "未提供"}</dd>
                    </div>
                    <div>
                      <dt>唯一编码</dt>
                      <dd>{equipment.source_unique_code ?? "未提供"}</dd>
                    </div>
                    <div>
                      <dt>生产日期</dt>
                      <dd>{equipment.manufactured_on ?? "未提供"}</dd>
                    </div>
                  </dl>
                  {equipment.connectors.map((connector) => (
                    <p key={connector.id}>
                      {connector.name ?? "未命名枪口"} · {connector.external_id}{" "}
                      · {connector.interface_type ?? "接口类型未提供"}
                    </p>
                  ))}
                </details>
              ))}
              {value.equipment.length === 0 && (
                <p className="empty">本次归档时无设备记录</p>
              )}
            </>
          )}
        </section>
      )}
      <h4>申请内容</h4>
      <p>
        {typeof value.payload.note === "string"
          ? value.payload.note
          : "未填写说明"}
      </p>
      {typeof value.payload.name === "string" && (
        <p>拟变更名称：{value.payload.name}</p>
      )}
      {typeof value.payload.address === "string" && (
        <p>拟变更地址：{value.payload.address}</p>
      )}
      <dl className="station-facts">
        <div>
          <dt>本次申请联系人</dt>
          <dd>
            {contact && "name" in contact && typeof contact.name === "string"
              ? contact.name
              : "未填写"}
          </dd>
        </div>
        <div>
          <dt>本次申请联系方式</dt>
          <dd>
            {contact && "phone" in contact && typeof contact.phone === "string"
              ? contact.phone
              : "未填写"}
          </dd>
        </div>
      </dl>
    </>
  );
}
export function ApplicationEvidencePanel({
  applicationId,
  version,
  status,
  revision,
  onReview,
}: {
  applicationId: number;
  version: number;
  status: string;
  revision: number;
  onReview: (state: ApplicationReviewState) => void;
}) {
  const [context, setContext] = useState<ApiSchemas["ReviewContext"] | null>(
      null,
    ),
    [page, setPage] = useState(1),
    [submissions, setSubmissions] = useState<
      ApiSchemas["SubmissionPage"] | null
    >(null),
    [selected, setSelected] = useState<number | null>(null),
    [snapshot, setSnapshot] = useState<ApiSchemas["SubmissionDetail"] | null>(
      null,
    ),
    [mode, setMode] = useState(
      ["draft", "rejected"].includes(status) ? "current" : "submitted",
    ),
    [nonce, setNonce] = useState(0),
    [error, setError] = useState(""),
    [snapshotError, setSnapshotError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    setMode(["draft", "rejected"].includes(status) ? "current" : "submitted");
    if (status === "submitted") setSelected(null);
  }, [status]);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    onReview({
      applicationId,
      version,
      revision,
      token: null,
      busy: true,
      categories: {},
    });
    typedApi
      .GET("/api/v1/applications/{application_id}/review-context", {
        params: { path: { application_id: applicationId } },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "送审资料读取失败"));
        setContext(data);
        onReview({
          applicationId,
          version: data.application_version,
          revision,
          token: data.review_token,
          busy: false,
          categories: data.category_options,
        });
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "送审资料读取失败");
          onReview({
            applicationId,
            version,
            revision,
            token: null,
            busy: false,
            categories: {},
          });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [applicationId, version, revision, nonce, onReview]);
  useEffect(() => {
    const controller = new AbortController();
    typedApi
      .GET("/api/v1/applications/{application_id}/submissions", {
        params: {
          path: { application_id: applicationId },
          query: { page, page_size: 10 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "送审版本列表读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 10));
        if (page > last) {
          setPage(last);
          return;
        }
        setSubmissions(data);
        setSelected((old) => old ?? data.items[0]?.id ?? null);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "送审版本列表读取失败");
      });
    return () => controller.abort();
  }, [applicationId, version, page, nonce]);
  useEffect(() => {
    if (selected == null) {
      setSnapshot(null);
      return;
    }
    const controller = new AbortController();
    setSnapshot(null);
    setSnapshotError("");
    typedApi
      .GET(
        "/api/v1/applications/{application_id}/submissions/{submission_id}",
        {
          params: {
            path: { application_id: applicationId, submission_id: selected },
          },
          signal: controller.signal,
          cache: "no-store",
        },
      )
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "送审版本读取失败"));
        setSnapshot(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setSnapshotError(e instanceof Error ? e.message : "送审版本读取失败");
      });
    return () => controller.abort();
  }, [applicationId, selected, version]);
  return (
    <section className="application-evidence" aria-label="申请送审资料">
      <div className="evidence-tabs">
        <button
          type="button"
          className="outline"
          aria-pressed={mode === "current"}
          onClick={() => setMode("current")}
        >
          当前来源资料
        </button>
        <button
          type="button"
          className="outline"
          aria-pressed={mode === "submitted"}
          onClick={() => setMode("submitted")}
        >
          历次送审版本
        </button>
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => setNonce((n) => n + 1)}
        >
          重新核对资料
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {mode === "current" && (
        <>
          {busy && <p role="status">正在读取当前资料…</p>}
          {context && (
            <>
              <p className="muted">
                当前来源资料 · 此处数据随来源更新，每次提交另行留存。
              </p>
              {context.application_version !== version && (
                <p role="alert" className="error">
                  申请记录已更新，请重新打开详情后核对。
                </p>
              )}
              <SubmittedContent value={context.content} />
              <p className="muted">{context.notice}</p>
            </>
          )}
        </>
      )}
      {mode === "submitted" && (
        <>
          <h3>历次送审版本</h3>
          {submissions && (
            <>
              {!submissions.items.length && (
                <p className="empty">
                  未留存送审版本。旧记录不使用当前来源资料回填历史。
                </p>
              )}
              <div className="submission-choices">
                {submissions.items.map((s) => (
                  <button
                    type="button"
                    className="outline"
                    aria-pressed={selected === s.id}
                    key={s.id}
                    onClick={() => setSelected(s.id)}
                  >
                    记录版本 {s.submitted_version} · {clock(s.submitted_at)}
                  </button>
                ))}
              </div>
              <div className="device-pagination">
                <small>
                  送审版本第{submissions.page}页 · 共{submissions.total}次提交
                </small>
                <div>
                  <button
                    className="outline"
                    aria-label="送审版本上一页"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="outline"
                    aria-label="送审版本下一页"
                    disabled={page * 10 >= submissions.total}
                    onClick={() => setPage(page + 1)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </>
          )}
          {snapshotError && (
            <p className="error" role="alert">
              {snapshotError}
            </p>
          )}
          {snapshot && (
            <article aria-label="冻结送审内容">
              <h4>提交时留存 · 记录版本 {snapshot.submitted_version}</h4>
              <p>
                提交账号：{snapshot.actor_username} ·{" "}
                {clock(snapshot.submitted_at)}（北京时间）
              </p>
              <p className="muted">
                提交前资料变更校验：
                {snapshot.source_reviewed ? "已通过" : "未进行"}
              </p>
              <SubmittedContent value={snapshot.content} />
              <h4>本次送审材料</h4>
              {snapshot.content.materials.length ? (
                <>
                  <ul>
                    {snapshot.content.materials.map((m) => (
                      <li key={m.id}>
                        <a
                          href={`/api/v1/applications/${applicationId}/submissions/${snapshot.id}/materials/${m.id}/download`}
                        >
                          {m.filename}
                        </a>{" "}
                        ·{" "}
                        {m.category
                          ? context?.category_options[m.category] || m.category
                          : "未分类"}
                      </li>
                    ))}
                  </ul>
                  <a
                    href={`/api/v1/applications/${applicationId}/submissions/${snapshot.id}/archive`}
                  >
                    下载本次送审材料整包
                  </a>
                </>
              ) : (
                <p className="muted">本次提交未附材料；正式必交清单待确认。</p>
              )}
              <p className="muted">{snapshot.notice}</p>
            </article>
          )}
        </>
      )}
    </section>
  );
}
