import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { pickStation } from "./station";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(module: string, args: string[]) {
  return execFileSync("uv", ["run", "python", "-m", module, ...args], {
    cwd: "../server",
    encoding: "utf8",
  });
}
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: user === "regulator" ? "告警数据" : "企业告警",
      exact: true,
    })
    .click();
  await page
    .getByRole("navigation", { name: "告警工作区" })
    .getByRole("button", { name: "来源与观测", exact: true })
    .click();
}
test("independent observations recover a lost response, freeze event evidence and separate monthly periods", async ({
  browser,
}) => {
  const station = JSON.parse(
      fixture("app.browser_fixture", ["create", "--located", "--operating"]),
    ),
    code = "browser-alertcfg-" + randomBytes(8).toString("hex"),
    contexts = await Promise.all([browser.newContext(), browser.newContext()]);
  try {
    const manager = await contexts[0].newPage(),
      owner = await contexts[1].newPage();
    await login(manager, "regulator");
    const me = await (await manager.request.get("/api/v1/auth/me")).json();
    const base = {
      code,
      version: 1,
      name: "浏览器独立分级",
      notifications_enabled: true,
      configuration: {
        configuration_version: 1,
        kind: "device_temperature",
        direction: "gte",
        period: "instant",
        thresholds: { general: "60", severe: "70", critical: "80" },
        recover_value: "50",
      },
    };
    const saved = await manager.request.post(
      "/api/v1/alerts/configured-rules",
      { headers: { "x-csrf-token": me.csrf_token }, data: base },
    );
    expect(saved.status(), await saved.text()).toBe(201);
    const rule = await saved.json();
    await manager
      .getByRole("button", { name: "刷新数据", exact: true })
      .click();
    const panel = manager.getByRole("region", {
      name: "独立分级告警观测",
      exact: true,
    });
    await pickStation(manager, "分级观测场站", station);
    await panel.getByLabel("查找分级观测规则", { exact: true }).fill(code);
    await panel
      .getByRole("button", { name: "筛选观测规则", exact: true })
      .click();
    await panel
      .getByLabel("分级观测规则", { exact: true })
      .selectOption(String(rule.id));
    const equipment = await (
      await manager.request.get(
        `/api/v1/alerts/configured-observations/equipment?station_id=${station.station_id}`,
      )
    ).json();
    await panel
      .getByLabel("分级观测设备", { exact: true })
      .selectOption(String(equipment.items[0].id));
    await panel.getByLabel("独立观测覆盖完整", { exact: true }).check();
    await panel.getByLabel("独立观测值", { exact: true }).fill("60");
    await panel
      .getByLabel("独立观测时间", { exact: true })
      .fill("2026-11-01T08:00:00");
    let lost = false,
      first: {
        observation_id: number;
        event_id: number;
        request_key: string;
      } | null = null;
    await manager.route(
      "**/api/v1/alerts/configured-observations",
      async (route) => {
        if (route.request().method() === "POST" && !lost) {
          lost = true;
          const response = await route.fetch();
          expect(response.status()).toBe(201);
          first = await response.json();
          await route.abort("failed");
        } else await route.continue();
      },
    );
    await panel
      .getByRole("button", { name: "提交独立演示观测", exact: true })
      .click();
    await expect(
      panel.getByRole("button", { name: "按原编号重试观测", exact: true }),
    ).toBeEnabled();
    await expect(
      panel.getByLabel("独立观测值", { exact: true }),
    ).toBeDisabled();
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "事件跟踪", exact: true })
      .click();
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "来源与观测", exact: true })
      .click();
    await expect(
      panel.getByRole("button", { name: "按原编号重试观测", exact: true }),
    ).toBeEnabled();
    await expect(
      panel.getByLabel("独立观测值", { exact: true }),
    ).toBeDisabled();
    const retry = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/alerts/configured-observations") &&
        r.request().method() === "POST",
    );
    await panel
      .getByRole("button", { name: "按原编号重试观测", exact: true })
      .click();
    const retried = await retry;
    expect(retried.status()).toBe(201);
    const receipt = await retried.json();
    expect(receipt).toEqual(first);
    expect(retried.request().postDataJSON().request_key).toBe(
      receipt.request_key,
    );
    let count = await (
      await manager.request.get(
        `/api/v1/alerts/configured-observations?station_id=${station.station_id}`,
      )
    ).json();
    expect(count.total).toBe(1);
    for (const [time, value, complete, result] of [
      ["2026-11-01T08:01:00", "80", true, "持续异常合并"],
      ["2026-11-01T08:02:00", "", false, "覆盖未知，未清除异常"],
    ] as const) {
      await expect(
        panel.getByLabel("独立观测值", { exact: true }),
      ).toBeEnabled();
      await panel.getByLabel("独立观测值", { exact: true }).fill(value);
      await panel.getByLabel("独立观测时间", { exact: true }).fill(time);
      await panel
        .getByLabel("独立观测覆盖完整", { exact: true })
        .setChecked(complete);
      await panel
        .getByRole("button", { name: "提交独立演示观测", exact: true })
        .click();
      await expect(
        panel.locator(".configured-observation-receipt"),
      ).toContainText(result);
    }
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "事件跟踪", exact: true })
      .click();
    await manager
      .getByLabel("告警检索编号", { exact: true })
      .fill(String(receipt.event_id));
    await manager
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await manager
      .getByRole("row")
      .filter({ hasText: `ALERT-${receipt.event_id}` })
      .getByRole("button", { name: "告警详情", exact: true })
      .click();
    const evidence = manager.getByRole("region", {
      name: "冻结分级告警依据",
      exact: true,
    });
    await expect(evidence).toContainText("OBS-");
    await expect(evidence).toContainText("最高等级：紧急");
    await expect(evidence).toContainText("80.000000");
    await manager
      .getByLabel("告警确认依据", { exact: true })
      .fill("已核对演示观测；人工确认不代表恢复");
    await manager
      .getByRole("button", { name: "确认已查看告警", exact: true })
      .click();
    await expect(
      manager.getByText("已核对演示观测；人工确认不代表恢复", { exact: true }),
    ).toBeVisible();
    const before = await (
      await manager.request.get(`/api/v1/alerts/${receipt.event_id}`)
    ).json();
    expect(before.status).toBe("active");
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "来源与观测", exact: true })
      .click();
    await panel.getByLabel("独立观测值", { exact: true }).fill("50");
    await panel
      .getByLabel("独立观测时间", { exact: true })
      .fill("2026-11-01T08:03:00");
    await panel.getByLabel("独立观测覆盖完整", { exact: true }).check();
    await panel
      .getByRole("button", { name: "提交独立演示观测", exact: true })
      .click();
    await expect(
      panel.locator(".configured-observation-receipt"),
    ).toContainText("恢复");
    const after = await (
      await manager.request.get(`/api/v1/alerts/${receipt.event_id}`)
    ).json();
    expect(after.status).toBe("recovered");
    expect(after.configuration_evidence.first).toEqual(
      before.configuration_evidence.first,
    );
    const monthly = await manager.request.post(
      "/api/v1/alerts/configured-rules",
      {
        headers: { "x-csrf-token": me.csrf_token },
        data: {
          ...base,
          version: 2,
          name: "浏览器周期分级",
          configuration: {
            ...base.configuration,
            kind: "station_fault",
            period: "natural_month",
            thresholds: { general: "5", critical: "20" },
            recover_value: "4",
          },
        },
      },
    );
    expect(monthly.status()).toBe(201);
    const monthlyRule = await monthly.json();
    await manager
      .getByRole("button", { name: "刷新数据", exact: true })
      .click();
    await panel
      .getByLabel("分级观测规则", { exact: true })
      .selectOption(String(monthlyRule.id));
    await panel.getByLabel("独立观测值", { exact: true }).fill("25");
    await panel
      .getByLabel("独立观测周期开始", { exact: true })
      .fill("2026-10-01");
    await panel
      .getByLabel("独立观测周期结束", { exact: true })
      .fill("2026-11-01");
    await panel
      .getByLabel("独立观测时间", { exact: true })
      .fill("2026-11-02T08:00:00");
    await panel
      .getByRole("button", { name: "提交独立演示观测", exact: true })
      .click();
    await expect(
      panel.locator(".configured-observation-receipt"),
    ).toContainText("触发");
    await panel
      .getByRole("button", { name: "查询独立观测", exact: true })
      .click();
    await expect(panel.getByRole("table")).toContainText("2026-10-01");
    await panel.screenshot({
      path: "../../.backups/configured-observations-browser.png",
    });
    await login(owner, "operator_a");
    const read = owner.getByRole("region", {
      name: "独立分级告警观测",
      exact: true,
    });
    await expect(
      read.getByRole("button", { name: "提交独立演示观测", exact: true }),
    ).toHaveCount(0);
    const own = await owner.request.get(
      `/api/v1/alerts/configured-observations?station_id=${station.station_id}`,
    );
    expect((await own.json()).total).toBe(5);
    count = await (
      await manager.request.get(
        `/api/v1/alerts/configured-observations?station_id=${station.station_id}`,
      )
    ).json();
    expect(count.total).toBe(5);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    fixture("app.browser_fixture", [
      "clean",
      "--station",
      String(station.station_id),
    ]);
    fixture("app.alert_configuration_fixture", ["--code", code]);
  }
});
