"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { businessStates } from "./station-map-types";
const number = (value: string | null) =>
  value == null
    ? "暂无观测"
    : value.includes(".")
      ? value.replace(/0+$/, "").replace(/\.$/, "")
      : value;
const time = (value: string | null) =>
  value == null
    ? "未留存 / 来源未提供"
    : new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      });
const kinds: Record<string, string> = {
  access: "接入",
  acceptance: "验收",
  commission: "投运",
  decommission: "核销",
  change: "信息变更",
};
const states: Record<string, string> = {
  draft: "草稿",
  submitted: "待审核",
  rejected: "驳回",
  approved: "已通过",
  withdrawn: "已撤回",
};
const actions: Record<string, string> = {
  create: "创建",
  submit: "提交",
  approve: "审核通过",
  reject: "驳回",
  withdraw: "撤回",
  edit: "编辑",
  upload: "上传材料",
  delete_attachment: "移除材料",
};
function initialDates() {
  const format = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const today = format.format(new Date());
  return {
    start_date: format.format(
      new Date(new Date(`${today}T00:00:00+08:00`).getTime() - 29 * 86400000),
    ),
    end_date: today,
    source: "",
  };
}
function EnergyHistory({ stationId }: { stationId: number }) {
  const [draft, setDraft] = useState(initialDates),
    [query, setQuery] = useState(initialDates),
    [data, setData] = useState<ApiSchemas["StationEnergyHistory"] | null>(null),
    [selected, setSelected] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    typedApi
      .GET("/api/v1/stations/{station_id}/energy-history", {
        params: { path: { station_id: stationId }, query },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "历史电量读取失败"));
        setData(data);
        setSelected((old) =>
          data.streams.some((s) => s.source === old)
            ? old
            : data.streams[0]?.source || "",
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "历史电量读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, query, revision]);
  const stream = data?.streams.find((s) => s.source === selected);
  return (
    <div aria-label="单站历史电量" aria-busy={busy}>
      <form
        className="device-query"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery({ ...draft });
        }}
      >
        <label>
          开始日期
          <input
            aria-label="单站电量开始日期"
            type="date"
            value={draft.start_date}
            onChange={(e) => setDraft({ ...draft, start_date: e.target.value })}
            required
          />
        </label>
        <label>
          结束日期
          <input
            aria-label="单站电量结束日期"
            type="date"
            value={draft.end_date}
            onChange={(e) => setDraft({ ...draft, end_date: e.target.value })}
            required
          />
        </label>
        <label>
          电量来源（留空查全部）
          <input
            aria-label="单站电量来源"
            value={draft.source}
            onChange={(e) => setDraft({ ...draft, source: e.target.value })}
            maxLength={50}
          />
        </label>
        <button className="outline" disabled={busy}>
          查询单站电量
        </button>
      </form>
      {busy && (
        <p role="status" className="muted">
          正在读取历史电量…
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}；保留上次查询结果。
        </p>
      )}
      {data && (
        <>
          <p className="muted">
            场站 #{data.station_id} · 已应用日期 {data.start_date} 至{" "}
            {data.end_date} · {data.requested_days}天（业务日期）
          </p>
          {!data.streams.length ? (
            <p className="empty">
              该窗口及来源没有留存独立日电量，不能据此判断电量为零。
            </p>
          ) : (
            <>
              <p className="device-scroll-hint muted">
                左右滚动可查看完整来源记录。
              </p>
              <div
                className="station-history-table"
                role="region"
                tabIndex={0}
                aria-label="单站电量来源汇总"
              >
                <table>
                  <thead>
                    <tr>
                      <th>独立来源</th>
                      <th>已知电量 kWh</th>
                      <th>观测 / 未观测天</th>
                      <th>参与 / 未参与记录</th>
                      <th>同日额外记录</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.streams.map((s) => (
                      <tr key={s.source}>
                        <td>{s.source}</td>
                        <td>{number(s.kwh)}</td>
                        <td>
                          {s.observed_days} / {s.missing_days}
                        </td>
                        <td>
                          {s.records} / {s.excluded_records}
                        </td>
                        <td>{s.extra_records_on_observed_days}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <label className="station-history-source">
                查看逐日电量
                <select
                  aria-label="历史电量明细来源"
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  {data.streams.map((s) => (
                    <option key={s.source} value={s.source}>
                      {s.source}
                    </option>
                  ))}
                </select>
              </label>
              {stream && (
                <div
                  className="station-history-table station-history-days"
                  role="region"
                  tabIndex={0}
                  aria-label={`逐日电量 ${stream.source}`}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>业务日</th>
                        <th>电量 kWh</th>
                        <th>参与记录</th>
                        <th>未参与记录</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stream.days.map((d) => (
                        <tr key={d.day}>
                          <td>{d.day}</td>
                          <td>{number(d.kwh)}</td>
                          <td>{d.records}</td>
                          <td>{d.excluded_records}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          <p className="muted">{data.notice}</p>
        </>
      )}
    </div>
  );
}
function ActionHistory({
  stationId,
  applicationId,
}: {
  stationId: number;
  applicationId: number;
}) {
  const [page, setPage] = useState(1),
    [data, setData] = useState<ApiSchemas["StationFlowActions"] | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    typedApi
      .GET("/api/v1/stations/{station_id}/flow/{application_id}/actions", {
        params: {
          path: { station_id: stationId, application_id: applicationId },
          query: { page, page_size: 10 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "流转动作读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 10));
        if (page > last) {
          setPage(last);
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "流转动作读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, applicationId, page, revision]);
  return (
    <div
      className="station-flow-actions"
      aria-label={`申请流转动作 ${applicationId}`}
      aria-busy={busy}
    >
      <h4>申请 #{applicationId} · 实际动作记录</h4>
      {busy && (
        <p role="status" className="muted">
          正在读取动作…
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {data && (
        <>
          <ol className="timeline-list">
            {data.items.map((a) => (
              <li className="timeline" key={a.id}>
                <b>{actions[a.action] || a.action}</b> · {time(a.created_at)}
                （北京时间）<p>操作账号：{a.actor_username}</p>
                {a.comment && <p>{a.comment}</p>}
              </li>
            ))}
          </ol>
          {!data.items.length && (
            <p className="empty">未留存动作记录，不从当前状态补造办理过程。</p>
          )}
          <div className="device-pagination">
            <small>
              第{data.page}页 · 共{data.total}条动作
            </small>
            <div>
              <button
                className="outline"
                aria-label="流转动作上一页"
                disabled={busy || page <= 1}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <button
                className="outline"
                aria-label="流转动作下一页"
                disabled={busy || page * 10 >= data.total}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
function StationFlow({ stationId }: { stationId: number }) {
  const [page, setPage] = useState(1),
    [data, setData] = useState<ApiSchemas["StationFlowPage"] | null>(null),
    [selected, setSelected] = useState<number | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const revision = usePageRefresh();
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    typedApi
      .GET("/api/v1/stations/{station_id}/flow", {
        params: {
          path: { station_id: stationId },
          query: { page, page_size: 10 },
        },
        signal: controller.signal,
        cache: "no-store",
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "接入流转读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 10));
        if (page > last) {
          setPage(last);
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "接入流转读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [stationId, page, revision]);
  return (
    <div aria-label="单站接入流转" aria-busy={busy}>
      {busy && (
        <p role="status" className="muted">
          正在读取接入流转…
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {data && (
        <>
          <dl className="station-facts">
            <div>
              <dt>平台当前状态</dt>
              <dd>
                {businessStates[data.platform_status] || data.platform_status}
              </dd>
            </div>
            <div>
              <dt>最近接入审核通过（平台）</dt>
              <dd>{time(data.last_access_approved_at)}</dd>
            </div>
            <div>
              <dt>首次接入（来源）</dt>
              <dd>{time(data.source_first_connected_at)}</dd>
            </div>
            <div>
              <dt>首次上报（来源）</dt>
              <dd>{time(data.source_first_reported_at)}</dd>
            </div>
            <div>
              <dt>最近同步落库（平台）</dt>
              <dd>{time(data.source_synced_at)}</dd>
            </div>
            <div>
              <dt>数据上报状态</dt>
              <dd>待确认正式时效规则</dd>
            </div>
          </dl>
          <p className="muted">以上时刻均按北京时间显示。</p>
          <div
            className="station-history-table"
            role="region"
            tabIndex={0}
            aria-label="场站申请记录"
          >
            <table>
              <thead>
                <tr>
                  <th>申请编号 / 类型</th>
                  <th>申请账号（当前）</th>
                  <th>状态</th>
                  <th>实际提交 / 审核通过</th>
                  <th>流转记录</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td>
                      #{a.id}
                      <br />
                      {kinds[a.kind] || a.kind}
                    </td>
                    <td>{a.applicant_username}</td>
                    <td>{states[a.status] || a.status}</td>
                    <td>
                      {time(a.last_submitted_at)}
                      <br />
                      {time(a.last_approved_at)}
                    </td>
                    <td>
                      <button
                        className="outline"
                        onClick={() => setSelected(a.id)}
                        aria-label={`查看流转动作 ${a.id}`}
                      >
                        {a.action_count}条动作
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.items.length && <p className="empty">未留存场站申请记录。</p>}
          <div className="device-pagination">
            <small>
              第{data.page}页 · 共{data.total}条申请
            </small>
            <div>
              <button
                className="outline"
                aria-label="场站流转上一页"
                disabled={busy || page <= 1}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <button
                className="outline"
                aria-label="场站流转下一页"
                disabled={busy || page * 10 >= data.total}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          </div>
          <p className="muted">{data.notice}</p>
        </>
      )}
      {selected != null && (
        <>
          <button className="outline" onClick={() => setSelected(null)}>
            关闭流转动作
          </button>
          <ActionHistory
            key={selected}
            stationId={stationId}
            applicationId={selected}
          />
        </>
      )}
    </div>
  );
}
export function StationObservations({ stationId }: { stationId: number }) {
  const stats = useCapability("statistics.read"),
    flow = useCapability("applications.read");
  const [energyEnabled, setEnergyEnabled] = useState(false),
    [flowEnabled, setFlowEnabled] = useState(false);
  if (!stats && !flow) return null;
  return (
    <section className="station-observations" aria-label="场站历史与流转">
      {stats && (
        <details
          onToggle={(e) => {
            if (e.currentTarget.open) setEnergyEnabled(true);
          }}
        >
          <summary>历史电量（独立来源）</summary>
          {energyEnabled && <EnergyHistory stationId={stationId} />}
        </details>
      )}
      {flow && (
        <details
          onToggle={(e) => {
            if (e.currentTarget.open) setFlowEnabled(true);
          }}
        >
          <summary>接入与流转记录</summary>
          {flowEnabled && <StationFlow stationId={stationId} />}
        </details>
      )}
    </section>
  );
}
