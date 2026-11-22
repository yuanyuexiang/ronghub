"use client";
import { useState } from "react";
import type { ApiSchemas } from "../lib/typed-api";
import {
  useBusinessQueue,
  loadRatingQueue,
  QueueFilters,
  QueuePagination,
} from "./business-queue";
import { BusinessExports } from "./business-exports";
export function SubsidyRuleOverview({
  policies,
}: {
  policies: ApiSchemas["SubsidyPolicyResponse"][];
}) {
  const [id, setId] = useState("");
  const selected = policies.find((p) => String(p.id) === id) ?? policies[0],
    rules = selected?.device_rules;
  return (
    <section className="prototype-rule-overview" aria-label="充换电补贴规则">
      <h3>充换电补贴规则</h3>
      <label>
        选定政策版本
        <select
          aria-label="规则配置政策版本"
          value={selected?.id ?? ""}
          onChange={(e) => setId(e.target.value)}
        >
          {policies.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.code} V{p.version}
            </option>
          ))}
        </select>
      </label>
      <div className="prototype-rule-grid">
        {[
          ["充电补贴标准", selected ? `${selected.rate} 元/度` : "待配置"],
          ["换电补贴标准", rules ? `${rules.swap_rate} 元/度` : "未配置"],
          [
            "补贴生成周期",
            rules
              ? { day: "日", month: "月", year: "年" }[rules.generation_period]
              : "未配置",
          ],
          [
            "故障率门槛",
            rules ? `严格小于 ${rules.fault_rate_below}%` : "未配置",
          ],
          [
            "离线率门槛",
            rules ? `严格小于 ${rules.offline_rate_below}%` : "未配置",
          ],
        ].map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </div>
      <h4>评分标准系数</h4>
      <div className="prototype-coefficients">
        {["0", "1", "2", "3", "4", "5"].map((star) => (
          <div key={star}>
            <span>{star === "0" ? "无星" : star + "星"}</span>
            <strong>
              {rules?.star_coefficients[
                star as keyof typeof rules.star_coefficients
              ] ?? "未配置"}
            </strong>
          </div>
        ))}
      </div>
      <p className="muted">
        选定版本只读。修改规则请创建新版本，已提交核算继续引用原版本；生成周期配置不代表定时生成服务已接通。
      </p>
    </section>
  );
}
export function RatingIndicatorCatalogue({
  standards,
}: {
  standards: ApiSchemas["RatingStandardResponse"][];
}) {
  const [id, setId] = useState("");
  const standard = standards.find((s) => String(s.id) === id) ?? standards[0];
  const categories = standard?.rules.categories?.length
    ? standard.rules.categories
    : [{ code: "general", name: "综合指标", weight: "1" }];
  return (
    <section
      className="prototype-indicator-catalogue"
      aria-label="分类评价指标"
    >
      <div className="prototype-query">
        <label>
          评价标准版本
          <select
            aria-label="指标目录标准版本"
            value={standard?.id ?? ""}
            onChange={(e) => setId(e.target.value)}
          >
            {standards.map((s) => (
              <option value={s.id} key={s.id}>
                {s.name} V{s.version}
              </option>
            ))}
          </select>
        </label>
        <div className="prototype-coefficients">
          {standard?.rules.thresholds?.map((t) => (
            <span key={t.stars}>
              {t.stars}星 ≥ {t.minimum}分
            </span>
          ))}
        </div>
      </div>
      {standard ? (
        <div className="table-wrap">
          <table
            className="indicator-catalogue-table"
            aria-label="分类指标及权重"
          >
            <thead>
              <tr>
                <th>指标名称</th>
                <th className="numeric-cell">指标权重</th>
                <th>数据来源</th>
                <th>数据维度</th>
                <th>状态</th>
                <th>生效日期</th>
                <th>说明</th>
              </tr>
            </thead>
            {categories.map((category) => (
              <tbody key={category.code}>
                <tr className="indicator-category">
                  <th scope="rowgroup" colSpan={7}>
                    {category.name} · 分类权重 {category.weight}
                  </th>
                </tr>
                {standard.rules.indicators
                  ?.filter(
                    (indicator) =>
                      !standard.rules.categories?.length ||
                      indicator.category === category.code,
                  )
                  .map((indicator) => (
                    <tr key={indicator.code}>
                      <td>{indicator.name}</td>
                      <td className="numeric-cell">{indicator.weight}</td>
                      <td>
                        {indicator.source === "runtime"
                          ? "平台数据"
                          : "专家 / 材料"}
                      </td>
                      <td>{indicator.field_name ?? "—"}</td>
                      <td>
                        {indicator.enabled == null
                          ? "历史未留存"
                          : indicator.enabled
                            ? "已启用"
                            : "已停用"}
                      </td>
                      <td>{indicator.effective_from ?? "未留存"}</td>
                      <td>{indicator.description ?? "—"}</td>
                    </tr>
                  ))}
              </tbody>
            ))}
          </table>
        </div>
      ) : (
        <p className="empty">暂无可展示的评价标准</p>
      )}
    </section>
  );
}
export function RatingCollaboration({
  onOpen,
  busy,
}: {
  onOpen: (id: number) => void;
  busy: boolean;
}) {
  const queue = useBusinessQueue(loadRatingQueue);
  const [selection, setSelection] = useState<number[]>([]);
  return (
    <section
      className="panel prototype-collaboration"
      aria-label="监管协同应用"
    >
      <div className="panel-title">
        <div>
          <h2>监管协同应用</h2>
          <p>按申报与场站核对同步范围；外部监管系统尚未接通</p>
        </div>
      </div>
      <QueueFilters
        queue={queue}
        label="协同申报"
        busy={busy}
        statuses={{
          submitted: "待评星",
          reviewed: "已复核",
          published: "已公示",
          delisted: "已摘牌",
        }}
        onSearch={() => setSelection([])}
      />
      <div className="prototype-shortcuts">
        <button disabled>同步当前结果</button>
        <button disabled>全部同步</button>
        <span>已勾选 {selection.length} 条 · 未执行外部同步</span>
      </div>
      <BusinessExports
        module="ratings"
        query={queue.query}
        busy={queue.loading || busy}
      />
      {queue.error && (
        <p className="error" role="alert">
          {queue.error}
        </p>
      )}
      <div className="table-wrap">
        <table className="aligned-records">
          <thead>
            <tr>
              {[
                "选择",
                "场站编号",
                "场站 / 运营商",
                "申报时间",
                "评星状态",
                "当前星级",
                "有效期至",
                "操作",
              ].map((s) => (
                <th
                  key={s}
                  className={s === "当前星级" ? "numeric-cell" : undefined}
                >
                  {s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {queue.rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <input
                    aria-label={`选择同步申报 ${row.id}`}
                    type="checkbox"
                    checked={selection.includes(row.id)}
                    onChange={(e) =>
                      setSelection(
                        e.target.checked
                          ? [...selection, row.id]
                          : selection.filter((id) => id !== row.id),
                      )
                    }
                  />
                </td>
                <td>{row.station_code || "未提供"}</td>
                <td>
                  {row.station_name}
                  <small>{row.operator_name}</small>
                </td>
                <td>
                  {row.last_submitted_at
                    ? new Date(row.last_submitted_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                      })
                    : "未留存"}
                </td>
                <td>
                  {{
                    draft: "草稿",
                    submitted: "待评星",
                    reviewed: "已复核",
                    published: "已公示",
                    delisted: "已摘牌",
                    rejected: "已退回",
                    withdrawn: "已撤回",
                    superseded: "已替代",
                    appealed: "申诉中",
                  }[row.status] ?? row.status}
                </td>
                <td className="numeric-cell">
                  {row.current_station_result?.effective_stars ?? "未确认"}
                </td>
                <td>{row.current_station_result?.valid_until ?? "未确认"}</td>
                <td>
                  <button
                    className="outline"
                    disabled={busy}
                    onClick={() => onOpen(row.id)}
                  >
                    查看申报档案
                  </button>
                  <button disabled>同步</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!queue.loading && !queue.rows.length && (
        <p className="empty">当前条件下暂无申报记录</p>
      )}
      <QueuePagination queue={queue} label="协同申报" busy={busy} />
    </section>
  );
}
