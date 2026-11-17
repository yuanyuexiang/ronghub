"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
type Preference = ApiSchemas["PreferenceResponse"];
type Category = Preference["category"];
type Recipient = ApiSchemas["RecipientResponse"];
const statusNames: Record<string, string> = {
  not_requested: "未开启短信",
  no_recipients: "无匹配接收人",
  simulated: "模拟回执（未实际发送）",
  provider_disabled: "通道禁用（未发送）",
};
function newRecipient() {
  return {
    id: null as number | null,
    version: 0,
    name: "",
    phone: "",
    categories: [] as Category[],
    enabled: true,
  };
}
export function AlertNotifications({ csrf }: { csrf: string }) {
  const [open, setOpen] = useState(false),
    [revision, setRevision] = useState(0),
    refresh = usePageRefresh();
  const [preferences, setPreferences] = useState<
      ApiSchemas["PreferenceList"] | null
    >(null),
    [preferenceError, setPreferenceError] = useState("");
  const [contacts, setContacts] = useState<ApiSchemas["RecipientPage"] | null>(
      null,
    ),
    [contactError, setContactError] = useState(""),
    [contactLoading, setContactLoading] = useState(false),
    [q, setQ] = useState(""),
    [query, setQuery] = useState({ q: "", page: 1 });
  const [preference, setPreference] = useState<{
      original: Preference;
      inApp: string;
      sms: boolean;
    } | null>(null),
    [recipient, setRecipient] = useState<ReturnType<
      typeof newRecipient
    > | null>(null),
    [busy, setBusy] = useState(false),
    [writeError, setWriteError] = useState("");
  const [showHistory, setShowHistory] = useState(false),
    [eventFilter, setEventFilter] = useState(""),
    [historyQuery, setHistoryQuery] = useState({ event_id: "", page: 1 }),
    [routes, setRoutes] = useState<ApiSchemas["RoutingPage"] | null>(null),
    [deliveries, setDeliveries] = useState<ApiSchemas["DeliveryPage"] | null>(
      null,
    ),
    [historyError, setHistoryError] = useState(""),
    [historyLoading, setHistoryLoading] = useState(false);
  const labels = Object.fromEntries(
    preferences?.items.map((row) => [row.category, row.label]) ?? [],
  );
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setPreferenceError("");
    typedApi
      .GET("/api/v1/alerts/notifications/preferences", {
        cache: "no-store",
        signal: controller.signal,
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "通知类型读取失败"));
        setPreferences(result.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setPreferenceError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      });
    return () => controller.abort();
  }, [open, revision, refresh]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setContactLoading(true);
    setContactError("");
    typedApi
      .GET("/api/v1/alerts/notifications/recipients", {
        cache: "no-store",
        signal: controller.signal,
        params: { query: { ...query, page_size: 20 } },
      })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.data)
          throw new Error(apiError(result.error, "接收人读取失败"));
        const last = Math.max(
          1,
          Math.ceil(result.data.total / result.data.page_size),
        );
        if (query.page > last) {
          setQuery({ ...query, page: last });
          return;
        }
        setContacts(result.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setContactError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setContactLoading(false);
      });
    return () => controller.abort();
  }, [open, query, revision, refresh]);
  useEffect(() => {
    if (!open || !showHistory) return;
    const controller = new AbortController();
    setHistoryLoading(true);
    setHistoryError("");
    const params = {
      query: {
        event_id: historyQuery.event_id
          ? Number(historyQuery.event_id)
          : undefined,
        page: historyQuery.page,
        page_size: 20,
      },
    };
    Promise.all([
      typedApi.GET("/api/v1/alerts/notifications/routings", {
        cache: "no-store",
        signal: controller.signal,
        params,
      }),
      typedApi.GET("/api/v1/alerts/notifications/deliveries", {
        cache: "no-store",
        signal: controller.signal,
        params,
      }),
    ])
      .then(([routing, sms]) => {
        if (controller.signal.aborted) return;
        if (!routing.data)
          throw new Error(apiError(routing.error, "路由历史读取失败"));
        if (!sms.data)
          throw new Error(apiError(sms.error, "短信模拟回执读取失败"));
        setRoutes(routing.data);
        setDeliveries(sms.data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setHistoryError(
            reason instanceof Error ? reason.message : "读取失败",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [open, showHistory, historyQuery, revision, refresh]);
  async function savePreference() {
    if (!preference) return;
    setBusy(true);
    setWriteError("");
    try {
      if (!preference.inApp) throw new Error("请明确选择站内通知开关");
      const result = await typedApi.PUT(
        "/api/v1/alerts/notifications/preferences/{category}",
        {
          headers: { "x-csrf-token": csrf },
          params: { path: { category: preference.original.category } },
          body: {
            version: preference.original.version,
            in_app_enabled: preference.inApp === "on",
            sms_enabled: preference.sms,
          },
        },
      );
      if (!result.data)
        throw new Error(
          apiError(result.error, "保存失败；请核对配置版本，原输入保留"),
        );
      setPreference(null);
      setRevision((v) => v + 1);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function saveRecipient() {
    if (!recipient) return;
    setBusy(true);
    setWriteError("");
    try {
      if (!recipient.categories.length) throw new Error("至少选择一项接收类型");
      const body = {
        name: recipient.name,
        phone: recipient.phone,
        categories: recipient.categories,
        enabled: recipient.enabled,
      };
      const result = recipient.id
        ? await typedApi.PATCH(
            "/api/v1/alerts/notifications/recipients/{recipient_id}",
            {
              headers: { "x-csrf-token": csrf },
              params: { path: { recipient_id: recipient.id } },
              body: { ...body, version: recipient.version },
            },
          )
        : await typedApi.POST("/api/v1/alerts/notifications/recipients", {
            headers: { "x-csrf-token": csrf },
            body,
          });
      if (!result.data)
        throw new Error(
          apiError(result.error, "保存失败，请核对手机号及版本；原输入保留"),
        );
      setRecipient(null);
      setQuery({ ...query, page: 1 });
      setRevision((v) => v + 1);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function remove(row: Recipient) {
    if (
      !window.confirm(
        "删除后停止接收，历史模拟回执继续保留。是否删除该接收人？",
      )
    )
      return;
    setBusy(true);
    setWriteError("");
    try {
      const result = await typedApi.DELETE(
        "/api/v1/alerts/notifications/recipients/{recipient_id}",
        {
          headers: { "x-csrf-token": csrf },
          params: {
            path: { recipient_id: row.id },
            query: { version: row.version },
          },
        },
      );
      if (!result.data)
        throw new Error(apiError(result.error, "删除失败，请刷新核对版本"));
      setRevision((v) => v + 1);
    } catch (reason) {
      setWriteError(reason instanceof Error ? reason.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel alert-notifications-panel"
      aria-label="告警通知配置"
    >
      <div className="panel-title">
        <div>
          <h3>告警通知配置</h3>
          <p>按类型管理站内通知、短信意图及接收人</p>
        </div>
        <button
          className="outline"
          disabled={busy}
          onClick={() => setOpen(!open)}
        >
          {open ? "收起通知配置" : "管理通知与接收人"}
        </button>
      </div>
      {open && (
        <div className="alert-notifications-body">
          <p className="muted">
            短信接收人仅用于通知，不作为登录手机或身份验证。本环境不会发送真实短信；历史回执按当时名单和配置保存。
          </p>
          {preferenceError && (
            <p className="error" role="alert">
              {preferenceError}
            </p>
          )}
          {writeError && (
            <p className="error" role="alert">
              {writeError}
            </p>
          )}
          {preferences && !preferenceError && (
            <>
              <p>{preferences.notice}</p>
              <div className="table-wrap">
                <table className="alert-notification-types">
                  <thead>
                    <tr>
                      {[
                        "通知类型",
                        "站内通知",
                        "短信意图",
                        "版本 / 修改时间",
                        "操作",
                      ].map((label) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preferences.items.map((row) => (
                      <tr key={row.category}>
                        <td>{row.label}</td>
                        <td>
                          {row.in_app_enabled === null
                            ? "沿用规则（未配置）"
                            : row.in_app_enabled
                              ? "开启"
                              : "关闭"}
                        </td>
                        <td>{row.sms_enabled ? "开启" : "关闭"}</td>
                        <td>
                          {row.version || "未配置"}
                          <small>
                            {row.updated_at
                              ? new Date(row.updated_at).toLocaleString(
                                  "zh-CN",
                                  { timeZone: "Asia/Shanghai" },
                                )
                              : "尚无类型配置记录"}
                          </small>
                        </td>
                        <td>
                          <button
                            className="outline"
                            disabled={busy}
                            aria-label={`配置通知 ${row.label}`}
                            onClick={() => {
                              setPreference({
                                original: row,
                                inApp:
                                  row.in_app_enabled === null
                                    ? ""
                                    : row.in_app_enabled
                                      ? "on"
                                      : "off",
                                sms: row.sms_enabled,
                              });
                              setWriteError("");
                            }}
                          >
                            配置
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {preference && (
            <form
              className="notification-editor"
              aria-label="通知类型表单"
              onSubmit={(e) => {
                e.preventDefault();
                void savePreference();
              }}
            >
              <fieldset disabled={busy}>
                <legend>
                  {preference.original.label} · 配置版本{" "}
                  {preference.original.version}
                </legend>
                <label>
                  站内开关
                  <select
                    aria-label="类型站内通知开关"
                    required
                    value={preference.inApp}
                    onChange={(e) =>
                      setPreference({ ...preference, inApp: e.target.value })
                    }
                  >
                    <option value="">请选择</option>
                    <option value="on">开启</option>
                    <option value="off">关闭</option>
                  </select>
                </label>
                <label>
                  <input
                    aria-label="类型短信通知开关"
                    type="checkbox"
                    checked={preference.sms}
                    onChange={(e) =>
                      setPreference({ ...preference, sms: e.target.checked })
                    }
                  />
                  开启短信意图（仅模拟/禁用回执）
                </label>
                <p className="muted">
                  站内通知同时受规则开关和类型开关控制；短信独立按类型及启用名单匹配。配置冲突不会自动覆盖他人修改。
                </p>
                <div className="action-row">
                  <button className="primary">保存通知类型</button>
                  <button
                    type="button"
                    className="outline"
                    onClick={() => setPreference(null)}
                  >
                    取消通知类型修改
                  </button>
                </div>
              </fieldset>
            </form>
          )}
          <div className="panel-title">
            <div>
              <h3>短信告警接收人</h3>
              <p>仅管理人员可读取完整号码，列表和回执显示脱敏号码</p>
            </div>
            <button
              className="outline"
              disabled={busy || !preferences}
              onClick={() => {
                setRecipient(newRecipient());
                setWriteError("");
              }}
            >
              新增告警接收人
            </button>
          </div>
          <form
            className="notification-search"
            onSubmit={(e) => {
              e.preventDefault();
              setQuery({ q, page: 1 });
            }}
          >
            <input
              aria-label="告警接收人检索"
              placeholder="姓名或手机号"
              value={q}
              maxLength={80}
              onChange={(e) => setQ(e.target.value)}
            />
            <button className="outline" disabled={busy || contactLoading}>
              查询告警接收人
            </button>
          </form>
          {contactError && (
            <p className="error" role="alert">
              {contactError}
            </p>
          )}
          {contactLoading && <p role="status">正在读取接收人…</p>}
          {contacts && !contactLoading && !contactError && (
            <>
              <div className="table-wrap">
                <table className="alert-recipient-table">
                  <thead>
                    <tr>
                      {[
                        "姓名",
                        "手机号",
                        "接收类型",
                        "启用 / 版本",
                        "操作",
                      ].map((label) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {contacts.items.map((row) => (
                      <tr key={row.id}>
                        <td>{row.name}</td>
                        <td>{row.masked_phone}</td>
                        <td>
                          {row.categories
                            .map((category) => labels[category] ?? category)
                            .join("、")}
                        </td>
                        <td>
                          {row.enabled ? "启用" : "停用"} / V{row.version}
                        </td>
                        <td>
                          <button
                            className="outline"
                            disabled={busy}
                            aria-label={`修改接收人 ${row.name}`}
                            onClick={() => {
                              setRecipient({ ...row });
                              setWriteError("");
                            }}
                          >
                            修改
                          </button>
                          <button
                            className="outline danger"
                            disabled={busy}
                            aria-label={`删除接收人 ${row.name}`}
                            onClick={() => remove(row)}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!contacts.items.length && (
                  <p className="empty">没有匹配的接收人</p>
                )}
              </div>
              <div className="notification-pagination">
                <button
                  className="outline"
                  disabled={busy || query.page <= 1}
                  onClick={() => setQuery({ ...query, page: query.page - 1 })}
                >
                  接收人上一页
                </button>
                <span>
                  第{contacts.page}页 · 共{contacts.total}人
                </span>
                <button
                  className="outline"
                  disabled={busy || query.page * 20 >= contacts.total}
                  onClick={() => setQuery({ ...query, page: query.page + 1 })}
                >
                  接收人下一页
                </button>
              </div>
            </>
          )}
          {recipient && (
            <form
              className="notification-editor"
              aria-label="告警接收人表单"
              onSubmit={(e) => {
                e.preventDefault();
                void saveRecipient();
              }}
            >
              <fieldset disabled={busy}>
                <legend>
                  {recipient.id
                    ? `修改接收人 · V${recipient.version}`
                    : "新增接收人"}
                </legend>
                <div className="notification-fields">
                  <label>
                    姓名
                    <input
                      aria-label="告警接收人姓名"
                      required
                      maxLength={80}
                      value={recipient.name}
                      onChange={(e) =>
                        setRecipient({ ...recipient, name: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    手机号
                    <input
                      aria-label="告警接收人手机号"
                      type="tel"
                      autoComplete="off"
                      required
                      maxLength={11}
                      pattern="1[3-9][0-9]{9}"
                      value={recipient.phone}
                      onChange={(e) =>
                        setRecipient({ ...recipient, phone: e.target.value })
                      }
                    />
                  </label>
                </div>
                <div className="notification-category-choices">
                  {preferences?.items.map((row) => (
                    <label key={row.category}>
                      <input
                        type="checkbox"
                        aria-label={`接收类型 ${row.label}`}
                        checked={recipient.categories.includes(row.category)}
                        onChange={(e) =>
                          setRecipient({
                            ...recipient,
                            categories: e.target.checked
                              ? [...recipient.categories, row.category]
                              : recipient.categories.filter(
                                  (category) => category !== row.category,
                                ),
                          })
                        }
                      />
                      {row.label}
                    </label>
                  ))}
                </div>
                <label>
                  <input
                    aria-label="启用告警接收人"
                    type="checkbox"
                    checked={recipient.enabled}
                    onChange={(e) =>
                      setRecipient({ ...recipient, enabled: e.target.checked })
                    }
                  />
                  启用接收
                </label>
                <div className="action-row">
                  <button className="primary">保存告警接收人</button>
                  <button
                    className="outline"
                    type="button"
                    onClick={() => setRecipient(null)}
                  >
                    取消接收人修改
                  </button>
                </div>
              </fieldset>
            </form>
          )}
          <div className="panel-title">
            <div>
              <h3>通知路由与短信模拟回执</h3>
              <p>模拟不是实际送达；历史记录不随接收人修改而重算</p>
            </div>
            <button
              className="outline"
              onClick={() => setShowHistory(!showHistory)}
            >
              {showHistory ? "收起通知记录" : "查看通知记录"}
            </button>
          </div>
          {showHistory && (
            <>
              <form
                className="notification-search"
                onSubmit={(e) => {
                  e.preventDefault();
                  setHistoryQuery({ event_id: eventFilter, page: 1 });
                }}
              >
                <input
                  aria-label="通知记录事件编号"
                  type="number"
                  min="1"
                  max="2147483647"
                  value={eventFilter}
                  onChange={(e) => setEventFilter(e.target.value)}
                  placeholder="告警事件编号"
                />
                <button className="outline" disabled={historyLoading}>
                  查询通知记录
                </button>
              </form>
              {historyLoading && <p role="status">读取通知记录…</p>}
              {historyError && (
                <p className="error" role="alert">
                  {historyError}
                </p>
              )}
              {!historyLoading && !historyError && routes && deliveries && (
                <>
                  <h4>路由意图（{routes.total}条）</h4>
                  <div className="table-wrap">
                    <table className="alert-routing-table">
                      <thead>
                        <tr>
                          {[
                            "事件 / 阶段",
                            "类型 / 配置版本",
                            "站内通知",
                            "短信结果",
                            "时间（北京时间）",
                          ].map((label) => (
                            <th key={label}>{label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {routes.items.map((row) => (
                          <tr key={row.id}>
                            <td>
                              ALERT-{row.event_id}
                              <small>
                                {(
                                  {
                                    opened: "触发",
                                    recovered: "恢复",
                                    level_severe: "升为严重",
                                    level_critical:
                                      row.category === "station_metric"
                                        ? "升为重大"
                                        : "升为紧急",
                                  } as Record<string, string>
                                )[row.phase] ?? "其他阶段"}
                              </small>
                            </td>
                            <td>
                              {row.category
                                ? labels[row.category]
                                : "未分类旧规则"}
                              <small>
                                {row.preference_version
                                  ? `V${row.preference_version}`
                                  : "沿用规则"}
                              </small>
                            </td>
                            <td>{row.in_app_enabled ? "生成" : "未生成"}</td>
                            <td>{statusNames[row.sms_status]}</td>
                            <td>
                              {new Date(row.created_at).toLocaleString(
                                "zh-CN",
                                { timeZone: "Asia/Shanghai" },
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <h4>短信回执（{deliveries.total}条）</h4>
                  <div className="table-wrap">
                    <table className="alert-sms-table">
                      <thead>
                        <tr>
                          {[
                            "事件 / 类型",
                            "接收人 / 当时版本",
                            "当时号码（脱敏）",
                            "结果",
                            "时间（北京时间）",
                          ].map((label) => (
                            <th key={label}>{label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {deliveries.items.map((row) => (
                          <tr key={row.id}>
                            <td>
                              ALERT-{row.event_id}
                              <small>{labels[row.category]}</small>
                            </td>
                            <td>
                              {row.recipient_name}
                              <small>V{row.recipient_version}</small>
                            </td>
                            <td>{row.masked_phone}</td>
                            <td>{statusNames[row.status]}</td>
                            <td>
                              {new Date(row.created_at).toLocaleString(
                                "zh-CN",
                                { timeZone: "Asia/Shanghai" },
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!deliveries.items.length && (
                      <p className="empty">
                        没有匹配的短信模拟回执；不推断历史实际发送状态
                      </p>
                    )}
                  </div>
                  <div className="notification-pagination">
                    <button
                      className="outline"
                      disabled={historyQuery.page <= 1}
                      onClick={() =>
                        setHistoryQuery({
                          ...historyQuery,
                          page: historyQuery.page - 1,
                        })
                      }
                    >
                      通知记录上一页
                    </button>
                    <span>第{historyQuery.page}页</span>
                    <button
                      className="outline"
                      disabled={
                        historyQuery.page * 20 >=
                        Math.max(routes.total, deliveries.total)
                      }
                      onClick={() =>
                        setHistoryQuery({
                          ...historyQuery,
                          page: historyQuery.page + 1,
                        })
                      }
                    >
                      通知记录下一页
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
