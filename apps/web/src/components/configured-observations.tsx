"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { useCapability } from "./permissions";
import { usePageRefresh } from "./page-refresh";
import { StationPicker } from "./station-picker";
type Rule = ApiSchemas["RuleResponse"];
type Input = ApiSchemas["ObservationInput"];
const outcomes: Record<string, string> = {
  opened: "触发",
  merged: "持续异常合并",
  recovered: "恢复",
  normal: "未达触发阈值",
  unknown_coverage: "覆盖未知，未清除异常",
  ignored_stale: "重复或迟到，未改变事件",
};
import { alertLevelLabel, alertUnits } from "../lib/alert-labels";
function beijingNow() {
  return new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 19);
}
export function ConfiguredObservations({
  role,
  csrf,
  onApplied,
}: {
  role: string;
  csrf: string;
  onApplied?: () => Promise<void>;
}) {
  const canWrite = useCapability("alerts.samples") && role === "regulator",
    revision = usePageRefresh();
  const [station, setStation] = useState(""),
    [rule, setRule] = useState<Rule | null>(null),
    [ruleRows, setRuleRows] = useState<Rule[]>([]),
    [ruleQuery, setRuleQuery] = useState({ q: "", page: 1 }),
    [ruleSearch, setRuleSearch] = useState(""),
    [ruleTotal, setRuleTotal] = useState(0);
  const [equipment, setEquipment] = useState<
      ApiSchemas["EquipmentOption"] | null
    >(null),
    [equipmentRows, setEquipmentRows] = useState<
      ApiSchemas["EquipmentOption"][]
    >([]),
    [equipmentQuery, setEquipmentQuery] = useState({ q: "", page: 1 }),
    [equipmentSearch, setEquipmentSearch] = useState(""),
    [equipmentTotal, setEquipmentTotal] = useState(0);
  const [value, setValue] = useState(""),
    [rated, setRated] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [observed, setObserved] = useState(beijingNow),
    [complete, setComplete] = useState(false);
  const [pending, setPending] = useState<Input | null>(null),
    [receipt, setReceipt] = useState<ApiSchemas["ObservationResult"] | null>(
      null,
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [optionsError, setOptionsError] = useState("");
  const [query, setQuery] = useState({ station_id: "", rule_id: "", page: 1 }),
    [data, setData] = useState<ApiSchemas["ObservationPage"] | null>(null),
    [loading, setLoading] = useState(false),
    [readError, setReadError] = useState(""),
    [reload, setReload] = useState(0),
    [optionsBusy, setOptionsBusy] = useState(false);
  const sequence = useRef(0),
    periodic = rule?.configuration.period !== "instant";
  const chooseStation = useCallback((id: string) => {
    setStation(id);
    setEquipment(null);
    setEquipmentQuery({ q: "", page: 1 });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setOptionsError("");
    typedApi
      .GET("/api/v1/alerts/configured-rules", {
        cache: "no-store",
        signal: controller.signal,
        params: { query: { ...ruleQuery, page_size: 20, enabled: true } },
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "分级规则选项读取失败"));
        setRuleRows(result.data.items);
        setRuleTotal(result.data.total);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setOptionsError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      });
    return () => controller.abort();
  }, [ruleQuery, revision]);
  useEffect(() => {
    setEquipmentRows([]);
    setEquipmentTotal(0);
    setOptionsBusy(false);
    if (!station) return;
    const controller = new AbortController();
    setOptionsBusy(true);
    typedApi
      .GET("/api/v1/alerts/configured-observations/equipment", {
        cache: "no-store",
        signal: controller.signal,
        params: {
          query: {
            station_id: Number(station),
            ...equipmentQuery,
            page_size: 20,
          },
        },
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "设备选项读取失败"));
        setEquipmentRows(result.data.items);
        setEquipmentTotal(result.data.total);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setOptionsError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setOptionsBusy(false);
      });
    return () => controller.abort();
  }, [station, equipmentQuery, revision]);
  useEffect(() => {
    const current = ++sequence.current,
      controller = new AbortController();
    setLoading(true);
    setReadError("");
    typedApi
      .GET("/api/v1/alerts/configured-observations", {
        cache: "no-store",
        signal: controller.signal,
        params: {
          query: {
            station_id: query.station_id ? Number(query.station_id) : undefined,
            rule_id: query.rule_id ? Number(query.rule_id) : undefined,
            page: query.page,
            page_size: 20,
          },
        },
      })
      .then((result) => {
        if (controller.signal.aborted || current !== sequence.current) return;
        if (!result.data)
          throw new Error(apiError(result.error, "观测记录读取失败"));
        const last = Math.max(
          1,
          Math.ceil(result.data.total / result.data.page_size),
        );
        if (query.page > last) {
          setQuery({ ...query, page: last });
          return;
        }
        setData(result.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted && current === sequence.current)
          setReadError(reason instanceof Error ? reason.message : "读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted && current === sequence.current)
          setLoading(false);
      });
    return () => controller.abort();
  }, [query, reload, revision]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const retrying = !!pending;
      let payload = pending;
      if (!payload) {
        if (!station || !rule) throw new Error("请选择场站及分级规则");
        if (!periodic && !equipment) throw new Error("请选择该场站的设备");
        if (periodic && (!start || !end)) throw new Error("请填写完整周期起止");
        if (!observed) throw new Error("请填写观测时间");
        payload = {
          request_key: crypto.randomUUID(),
          station_id: Number(station),
          rule_id: rule.id,
          equipment_id: periodic ? null : equipment!.id,
          value: value.trim() || null,
          rated_current_a:
            rule.configuration.kind === "device_current"
              ? rated.trim() || null
              : null,
          period_start: periodic ? start : null,
          period_end: periodic ? end : null,
          coverage_complete: complete,
          observed_at:
            observed + (observed.length === 16 ? ":00" : "") + "+08:00",
        };
        setReceipt(null);
        setPending(payload);
      }
      const result = await typedApi.POST(
        "/api/v1/alerts/configured-observations",
        { headers: { "x-csrf-token": csrf }, body: payload },
      );
      if (!result.data) {
        if (
          result.response.status >= 400 &&
          result.response.status < 500 &&
          !retrying
        )
          setPending(null);
        throw new Error(
          apiError(result.error, "提交结果尚未确认，请按原编号重试或查询回执"),
        );
      }
      setReceipt(result.data);
      setObserved(beijingNow());
      setPending(null);
      setReload((v) => v + 1);
      if (onApplied) {
        try {
          await onApplied();
        } catch {
          setError("观测已保存，告警队列刷新失败，请手动刷新");
        }
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "提交结果尚未确认，请查询原请求回执",
      );
    } finally {
      setBusy(false);
    }
  }
  async function check() {
    if (!pending) return;
    setBusy(true);
    setError("");
    try {
      const result = await typedApi.GET(
        "/api/v1/alerts/configured-observations",
        {
          cache: "no-store",
          params: { query: { request_key: pending.request_key } },
        },
      );
      if (!result.data) throw new Error(apiError(result.error, "回执查询失败"));
      const row = result.data.items.find(
        (item) => item.request_key === pending.request_key,
      );
      if (!row)
        throw new Error("尚未查到回执，原请求可能仍在处理；可保持原编号重试");
      setReceipt(row.result);
      setPending(null);
      setReload((v) => v + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "查询失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel configured-observations"
      aria-label="独立分级告警观测"
    >
      <div className="panel-title">
        <div>
          <h3>独立分级告警观测</h3>
          <p>设备匹配、闭合周期与固定编号回执 · 演示</p>
        </div>
      </div>
      <p className="configured-observations-notice">
        输入为人工演示观测，不代表客户真实数据。设备告警按实测值判断；场站周期百分比须有完整覆盖声明。未知不按零值或正常处理，不同周期独立留档。
      </p>
      {optionsError && (
        <p className="error" role="alert">
          {optionsError}
        </p>
      )}
      <fieldset
        className="configured-observations-fields"
        disabled={busy || !!pending}
      >
        <legend>观测对象与筛选</legend>
        <StationPicker
          label="分级观测场站"
          value={station}
          onChange={chooseStation}
          kind="alerts"
          allowAll
        />
        <div className="configured-options">
          <label>
            分级观测规则
            <select
              aria-label="分级观测规则"
              value={rule?.id ?? ""}
              onChange={(e) => {
                setRule(
                  ruleRows.find((row) => row.id === Number(e.target.value)) ??
                    null,
                );
                setReceipt(null);
              }}
            >
              <option value="">请选择规则 / 全部规则</option>
              {rule && !ruleRows.some((row) => row.id === rule.id) && (
                <option value={rule.id}>
                  {rule.name} V{rule.version}
                </option>
              )}
              {ruleRows.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name} V{row.version}
                </option>
              ))}
            </select>
          </label>
          <div className="action-row">
            <input
              aria-label="查找分级观测规则"
              placeholder="名称或代码"
              maxLength={150}
              value={ruleSearch}
              onChange={(e) => setRuleSearch(e.target.value)}
            />
            <button
              className="outline"
              onClick={() => setRuleQuery({ q: ruleSearch, page: 1 })}
            >
              筛选观测规则
            </button>
          </div>
          <div className="action-row">
            <button
              className="outline"
              disabled={ruleQuery.page <= 1}
              onClick={() =>
                setRuleQuery({ ...ruleQuery, page: ruleQuery.page - 1 })
              }
            >
              观测规则上一页
            </button>
            <small>
              {ruleQuery.page}页 / {ruleTotal}条
            </small>
            <button
              className="outline"
              disabled={ruleQuery.page * 20 >= ruleTotal}
              onClick={() =>
                setRuleQuery({ ...ruleQuery, page: ruleQuery.page + 1 })
              }
            >
              观测规则下一页
            </button>
          </div>
        </div>
        {canWrite && rule && (
          <>
            {!periodic && (
              <div className="configured-options">
                <label>
                  所属设备
                  <select
                    aria-label="分级观测设备"
                    disabled={!station || optionsBusy}
                    value={equipment?.id ?? ""}
                    onChange={(e) =>
                      setEquipment(
                        equipmentRows.find(
                          (row) => row.id === Number(e.target.value),
                        ) ?? null,
                      )
                    }
                  >
                    <option value="">请选择设备</option>
                    {equipment &&
                      !equipmentRows.some((row) => row.id === equipment.id) && (
                        <option value={equipment.id}>
                          {equipment.name} · {equipment.external_id}
                        </option>
                      )}
                    {equipmentRows.map((row) => (
                      <option value={row.id} key={row.id}>
                        {row.name} · {row.external_id}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="action-row">
                  <input
                    aria-label="查找分级观测设备"
                    placeholder="设备名称或编码"
                    maxLength={150}
                    value={equipmentSearch}
                    onChange={(e) => setEquipmentSearch(e.target.value)}
                  />
                  <button
                    className="outline"
                    onClick={() =>
                      setEquipmentQuery({ q: equipmentSearch, page: 1 })
                    }
                  >
                    筛选观测设备
                  </button>
                </div>
                <div className="action-row">
                  <button
                    className="outline"
                    disabled={equipmentQuery.page <= 1}
                    onClick={() =>
                      setEquipmentQuery({
                        ...equipmentQuery,
                        page: equipmentQuery.page - 1,
                      })
                    }
                  >
                    观测设备上一页
                  </button>
                  <small>
                    {equipmentQuery.page}页 / {equipmentTotal}条
                  </small>
                  <button
                    className="outline"
                    disabled={equipmentQuery.page * 20 >= equipmentTotal}
                    onClick={() =>
                      setEquipmentQuery({
                        ...equipmentQuery,
                        page: equipmentQuery.page + 1,
                      })
                    }
                  >
                    观测设备下一页
                  </button>
                </div>
              </div>
            )}
            <label>
              独立观测值
              {rule.configuration.kind === "device_current"
                ? "（A）"
                : `（${rule.unit}）`}
              <input
                aria-label="独立观测值"
                type="number"
                step="0.000001"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </label>
            {rule.configuration.kind === "device_current" && (
              <label>
                来源额定电流 A
                <input
                  aria-label="独立观测额定电流"
                  type="number"
                  step="0.000001"
                  value={rated}
                  onChange={(e) => setRated(e.target.value)}
                />
              </label>
            )}
            {periodic && (
              <>
                <label>
                  周期开始（北京时间）
                  <input
                    aria-label="独立观测周期开始"
                    type="date"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label>
                  周期结束（不含该日）
                  <input
                    aria-label="独立观测周期结束"
                    type="date"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
              </>
            )}
            <label>
              来源观测时间（北京时间）
              <input
                aria-label="独立观测时间"
                type="datetime-local"
                step="1"
                value={observed}
                onChange={(e) => setObserved(e.target.value)}
              />
            </label>
            <label className="configured-coverage">
              <input
                aria-label="独立观测覆盖完整"
                type="checkbox"
                checked={complete}
                onChange={(e) => setComplete(e.target.checked)}
              />
              本次观测覆盖完整（仅演示声明）
            </label>
          </>
        )}
      </fieldset>
      <div className="configured-observations-actions">
        <button
          className="outline"
          disabled={loading || busy}
          onClick={() =>
            setQuery({
              station_id: station,
              rule_id: rule ? String(rule.id) : "",
              page: 1,
            })
          }
        >
          查询独立观测
        </button>
        {canWrite && (
          <>
            <button
              className="primary"
              disabled={busy || (!pending && !rule)}
              onClick={submit}
            >
              {pending ? "按原编号重试观测" : "提交独立演示观测"}
            </button>
            {pending && (
              <button className="outline" disabled={busy} onClick={check}>
                查询原观测回执
              </button>
            )}
          </>
        )}
      </div>
      {pending && (
        <button
          className="outline"
          disabled={busy}
          onClick={() => {
            if (
              window.confirm(
                "原请求可能已经完成。请先查询并核对原回执；确认结束重试并重新填报？",
              )
            ) {
              setPending(null);
              setError("");
            }
          }}
        >
          结束原请求并重新核对
        </button>
      )}
      {pending && (
        <p className="configured-observations-notice">
          待确认请求：{pending.request_key}
          。输入已冻结；重试沿用原编号，不自动新建。
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {receipt && (
        <p className="configured-observation-receipt" role="status">
          观测 #{receipt.observation_id} · {outcomes[receipt.outcome]}
          {receipt.level
            ? ` · ${alertLevelLabel(receipt.level, rule?.configuration.period)}`
            : ""}{" "}
          · {receipt.event_id ? `ALERT-${receipt.event_id}` : "未生成事件"}
          <small>
            比较值 {receipt.comparison_value ?? "未知"} · 请求{" "}
            {receipt.request_key}
          </small>
        </p>
      )}
      <p className="configured-observations-notice">
        已应用：
        {query.station_id ? `场站 #${query.station_id}` : "全部授权场站"} ·{" "}
        {query.rule_id ? `规则 #${query.rule_id}` : "全部规则"}
        。未知或迟到记录保留，事件展示最近完整判定依据。
      </p>
      {loading && <p role="status">读取观测记录…</p>}
      {readError && (
        <p className="error" role="alert">
          {readError}
        </p>
      )}
      {data && !loading && !readError && (
        <>
          <div className="table-wrap">
            <table className="configured-observations-table">
              <thead>
                <tr>
                  {[
                    "编号 / 规则",
                    "场站 / 企业",
                    "冻结设备 / 周期",
                    "来源时间 / 覆盖",
                    "原值 / 比较值",
                    "结果 / 事件",
                  ].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>
                      OBS-{row.id}
                      <small>
                        {row.evidence.rule_code} V{row.evidence.rule_version}
                      </small>
                    </td>
                    <td>
                      {row.station_name}
                      <small>{row.operator_name}</small>
                    </td>
                    <td>
                      {row.evidence.equipment?.name ?? "场站周期"}
                      <small>
                        {row.evidence.equipment?.external_id ??
                          `${row.evidence.period_start} 至 ${row.evidence.period_end}（不含）`}
                      </small>
                    </td>
                    <td>
                      {new Date(row.evidence.observed_at).toLocaleString(
                        "zh-CN",
                        { timeZone: "Asia/Shanghai" },
                      )}
                      <small>
                        {row.evidence.coverage_complete
                          ? "覆盖完整（演示声明）"
                          : "覆盖未知"}
                      </small>
                    </td>
                    <td>
                      {row.evidence.value ?? "未知"}{" "}
                      {row.evidence.configuration.kind === "device_current"
                        ? "A"
                        : alertUnits[row.evidence.configuration.kind]}
                      <small>
                        比较 {row.evidence.comparison_value ?? "未知"}{" "}
                        {alertUnits[row.evidence.configuration.kind]}
                        {row.evidence.rated_current_a
                          ? ` / 额定 ${row.evidence.rated_current_a} A`
                          : ""}
                      </small>
                    </td>
                    <td>
                      {outcomes[row.result.outcome]}
                      <small>
                        {row.result.level
                          ? alertLevelLabel(
                              row.result.level,
                              row.evidence.configuration.period,
                            )
                          : ""}{" "}
                        {row.result.event_id
                          ? `ALERT-${row.result.event_id}`
                          : "无事件"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.items.length && (
              <p className="empty">该范围没有独立观测记录</p>
            )}
          </div>
          <div className="configured-observations-actions">
            <button
              className="outline"
              disabled={query.page <= 1}
              onClick={() => setQuery({ ...query, page: query.page - 1 })}
            >
              独立观测上一页
            </button>
            <span>
              第{data.page}页 · 共{data.total}条
            </span>
            <button
              className="outline"
              disabled={query.page * 20 >= data.total}
              onClick={() => setQuery({ ...query, page: query.page + 1 })}
            >
              独立观测下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
