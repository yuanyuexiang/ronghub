import { test, expect, navigateWorkspace, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("workbench shows scoped stock and independent periods while refresh keeps applied dates and drafts", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--operating",
        "--located",
        "--order-history",
        "2",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all(
    [0, 1, 2].map(() => browser.newContext({ timezoneId: "America/New_York" })),
  );
  try {
    const [own, reg, association] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await login(own, "operator_a");
    await expect(
      own.getByRole("heading", { name: "充电站排名" }),
    ).toBeVisible();
    await expect(own.getByLabel("运营周期总览", { exact: true })).toHaveCount(
      0,
    );
    await own.getByRole("button", { name: "审核结果", exact: true }).click();
    await expect(
      own.getByRole("heading", { name: "场站接入进展查看", level: 1 }),
    ).toBeVisible();
    await own.goBack();
    await expect(
      own.getByRole("heading", { name: "充电站排名" }),
    ).toBeVisible();
    await navigateWorkspace(own, "经营观测与接入记录");
    await expect(own.getByRole("heading", { name: "充电站排名" })).toHaveCount(
      0,
    );
    const panel = own.getByLabel("运营周期总览", { exact: true });
    await own.getByLabel("总览观测周期", { exact: true }).selectOption("month");
    await own.getByLabel("总览周期日期", { exact: true }).fill("2026-11-15");
    const pending = own.waitForResponse(
      (response) =>
        response.url().includes("/dashboard/operations?") &&
        response.url().includes("2026-11-15"),
    );
    await own
      .getByRole("button", { name: "查询运营总览", exact: true })
      .click();
    const result = await pending;
    expect(result.status()).toBe(200);
    const data = await result.json();
    expect(data.start_at).toBe("2026-11-01T00:00:00+08:00");
    await expect(panel).toContainText("2026-11-01 至 2026-11-30");
    const stream = data.orders.find(
      (value: { source: string }) => value.source === "browser-test",
    );
    expect(stream.completed_orders).toBeGreaterThanOrEqual(2);
    await expect(own.getByLabel("总览订单观测", { exact: true })).toContainText(
      `已完成 ${stream.completed_orders} / 观测 ${stream.observed_orders} 单`,
    );
    await expect(own.getByLabel("场站业务状态", { exact: true })).toContainText(
      "运营中",
    );
    await expect(
      own.getByLabel("最近12个月日电量趋势", { exact: true }),
    ).toContainText("2025-12-01 至 2026-11-15");
    await expect(panel).toContainText("正式审核通过率：待确认");
    await panel.screenshot({
      path: "test-results/dashboard-operations-desktop.png",
    });
    await own.setViewportSize({ width: 768, height: 1024 });
    await panel.screenshot({
      path: "test-results/dashboard-operations-narrow.png",
    });
    await own.getByLabel("总览周期日期", { exact: true }).fill("2024-02-29");
    await own
      .getByRole("button", { name: "查询运营总览", exact: true })
      .click();
    await expect(panel).toContainText("2024-02-01 至 2024-02-29");
    await own.getByLabel("总览周期日期", { exact: true }).fill("2023-01-02");
    await own.getByLabel("总览观测周期", { exact: true }).selectOption("year");
    const refresh = own.waitForResponse(
      (response) =>
        response.url().includes("/dashboard/operations?") &&
        response.url().includes("2024-02-29"),
    );
    await own.getByRole("button", { name: "刷新数据", exact: true }).click();
    expect(new URL((await refresh).url()).searchParams.get("period")).toBe(
      "month",
    );
    await expect(own.getByLabel("总览周期日期", { exact: true })).toHaveValue(
      "2023-01-02",
    );
    await expect(panel).toContainText("2024-02-01 至 2024-02-29");
    await login(reg, "regulator");
    await expect(reg.getByLabel("运营周期总览", { exact: true })).toBeVisible();
    for (const [shortcut, control, status] of [
      ["补贴待审", "补贴筛选状态", "submitted"],
      ["星级待审", "星级筛选状态", "submitted"],
      ["人员账号待审", "人员申请状态筛选", "pending"],
    ] as const) {
      await reg.getByRole("button", { name: shortcut, exact: true }).click();
      await expect(reg.getByLabel(control, { exact: true })).toHaveValue(
        status,
      );
      await reg.goBack();
      await expect(
        reg.getByRole("heading", { name: "运营总览", level: 1 }),
      ).toBeVisible();
    }
    await login(association, "association");
    await navigateWorkspace(association, "运营总览");
    await association
      .getByRole("button", { name: "待评审", exact: true })
      .click();
    await expect(
      association.getByLabel("星级筛选状态", { exact: true }),
    ).toHaveValue("submitted");
    await association.goBack();
    await association
      .getByRole("button", { name: "待公示", exact: true })
      .click();
    await expect(
      association.getByLabel("星级筛选状态", { exact: true }),
    ).toHaveValue("reviewed");
    await association
      .getByRole("button", { name: "返回评价结果管理", exact: true })
      .click();
    await expect(
      association.getByRole("heading", { name: "评价结果管理", level: 1 }),
    ).toBeVisible();
    await expect(
      association.getByLabel("运营周期总览", { exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await association.request.get(
          "/api/v1/dashboard/operations?period_date=2026-11-15",
        )
      ).status(),
    ).toBe(403);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "clean",
        "--station",
        String(fixture.station_id),
      ],
      { cwd: "../server" },
    );
  }
});
