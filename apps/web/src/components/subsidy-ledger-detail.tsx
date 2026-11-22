"use client";

import { useEffect, useState } from "react";
import { apiError, typedApi, type ApiSchemas } from "../lib/typed-api";
import { usePageRefresh } from "./page-refresh";
import { useCapability } from "./permissions";
import { useRecordFocus } from "./use-record-focus";

type LedgerRecord = ApiSchemas["LedgerItem"];

function localTime(value: string | null | undefined) {
  return value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未留存";
}

export function SubsidyLedgerDetail({
  record,
  busy,
  onBack,
  onOpenApplication,
}: {
  record: LedgerRecord;
  busy: boolean;
  onBack: () => void;
  onOpenApplication: () => void;
}) {
  const [showOrders, setShowOrders] = useState(false);
  const [page, setPage] = useState(1);
  const [orders, setOrders] = useState<ApiSchemas["ChargingOrderPage"] | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const canOrders = useCapability("orders.read");
  const canExport = useCapability("subsidy.export");
  const canExportOrders = useCapability("orders.export");
  const revision = usePageRefresh();
  const workspaceRef = useRecordFocus(
    record.id,
    showOrders ? "orders" : "detail",
  );
  const claimId = record.id;
  useEffect(() => {
    if (!showOrders || !canOrders) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/subsidy/ledger/{claim_id}/orders", {
        params: { path: { claim_id: claimId }, query: { page } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "读取参考订单失败"));
        const last = Math.max(1, Math.ceil(data.total / data.page_size));
        if (page > last) setPage(last);
        else setOrders(data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setOrders(null);
          setError(
            reason instanceof Error ? reason.message : "读取参考订单失败",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [claimId, showOrders, canOrders, page, revision]);

  async function download() {
    setExporting(true);
    setExportError("");
    try {
      const path = showOrders
        ? "/api/v1/subsidy/ledger/{claim_id}/orders/export"
        : "/api/v1/subsidy/ledger/{claim_id}/export";
      const { data, error } = await typedApi.GET(path, {
        params: { path: { claim_id: claimId } },
        parseAs: "blob",
        cache: "no-store",
      });
      if (!data) throw new Error(apiError(error, "导出失败"));
      const url = URL.createObjectURL(data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `SUB-${claimId}-${showOrders ? "参考订单" : "补贴明细"}.csv`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }

  return (
    <section
      ref={workspaceRef}
      className="panel record-detail ledger-detail"
      aria-label={showOrders ? "订单明细" : "补贴明细"}
    >
      <div className="panel-title">
        <div>
          <h2>{showOrders ? "订单明细" : "补贴明细"}</h2>
          <p>
            SUB-{record.id} · {record.station_name} · {record.start_date} 至{" "}
            {record.end_date}
          </p>
        </div>
        <div className="action-row">
          {canExport && (!showOrders || canExportOrders) && (
            <button
              className="outline"
              disabled={exporting || (showOrders && (loading || !orders))}
              onClick={() => void download()}
            >
              {showOrders ? "导出参考订单" : "导出补贴明细"}
            </button>
          )}
          <button
            className="outline"
            onClick={showOrders ? () => setShowOrders(false) : onBack}
          >
            {showOrders ? "返回补贴明细" : "返回补贴台账"}
          </button>
        </div>
      </div>
      {exportError && (
        <p className="error inline-note" role="alert">
          {exportError}
        </p>
      )}
      {showOrders ? (
        <>
          <p className="inline-note">
            {orders?.notice ??
              "以下按场站和核算周期查询源报订单，仅供核对。当前记录无逐笔订单关联。"}
          </p>
          {loading && (
            <p className="empty" role="status">
              正在读取参考订单…
            </p>
          )}
          {error && (
            <p className="error inline-note" role="alert">
              {error}
            </p>
          )}
          {!loading && orders && (
            <>
              {orders.items.length ? (
                <div
                  className="table-wrap"
                  role="region"
                  aria-label="参考订单列表，可横向滚动"
                  tabIndex={0}
                >
                  <table>
                    <thead>
                      <tr>
                        {[
                          "订单号",
                          "充电量（度）",
                          "场站名称",
                          "源桩编码",
                          "终端编码",
                          "开始时间（北京）",
                          "订单状态",
                        ].map((label) => (
                          <th scope="col" key={label}>
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {orders.items.map((order) => (
                        <tr key={order.id}>
                          <td>
                            {order.external_id}
                            <small>
                              {order.source} · {order.is_demo ? "演示" : "源报"}
                            </small>
                          </td>
                          <td className="numeric-cell">
                            {order.kwh ?? "未上报"}
                          </td>
                          <td>{order.station_name}</td>
                          <td>{order.equipment_external_id ?? "未上报"}</td>
                          <td>{order.connector_external_id ?? "未上报"}</td>
                          <td>{localTime(order.started_at)}</td>
                          <td>{orderStatus[order.status] ?? "未知"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">该场站在此核算周期内暂无源报订单</p>
              )}
              <div className="publication-pagination">
                <button
                  className="outline"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  订单上一页
                </button>
                <span>
                  第{page}页 · 共{orders.total}条
                </span>
                <button
                  className="outline"
                  disabled={page * orders.page_size >= orders.total}
                  onClick={() => setPage(page + 1)}
                >
                  订单下一页
                </button>
              </div>
            </>
          )}
        </>
      ) : (
        <>
          <dl className="station-facts claim-summary">
            <div>
              <dt>申请年度</dt>
              <dd>{record.application_year ?? "未留存"}</dd>
            </div>
            <div>
              <dt>补贴生成时间</dt>
              <dd>{localTime(record.generated_at)}</dd>
            </div>
            <div>
              <dt>核定时间</dt>
              <dd>{localTime(record.approved_at)}</dd>
            </div>
            <div>
              <dt>发放登记时间</dt>
              <dd>{localTime(record.recorded_disbursement_at)}</dd>
            </div>
          </dl>
          <div
            className="table-wrap"
            role="region"
            aria-label="场站补贴明细，可横向滚动"
            tabIndex={0}
          >
            <table>
              <thead>
                <tr>
                  {[
                    "场站名称",
                    "核算电量（度）",
                    "汇算补贴（元）",
                    "核定补贴（元）",
                    "操作",
                  ].map((label) => (
                    <th scope="col" key={label}>
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>
                    {record.station_name}
                    <small>{record.operator_name}</small>
                  </td>
                  <td className="numeric-cell">{record.total_kwh}</td>
                  <td className="numeric-cell">{record.base_amount}</td>
                  <td className="numeric-cell">{record.adjusted_amount}</td>
                  <td>
                    <div className="action-row">
                      {canOrders && (
                        <button
                          className="outline"
                          onClick={() => setShowOrders(true)}
                        >
                          订单明细
                        </button>
                      )}
                      <button
                        className="outline"
                        disabled={busy}
                        onClick={onOpenApplication}
                      >
                        查看申请核算依据
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="inline-note">
            核定金额与发放登记供台账核对。资金实际到账时间未留存。
          </p>
        </>
      )}
    </section>
  );
}

const orderStatus: Record<string, string> = {
  unknown: "未知",
  charging: "充电中",
  completed: "已完成",
  interrupted: "已中断",
  cancelled: "已取消",
};
