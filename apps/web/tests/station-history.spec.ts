import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function create() {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--station-history",
        "--facilities",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
}
function clean(id: number) {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "app.browser_fixture",
      "clean",
      "--station",
      String(id),
    ],
    { cwd: "../server", stdio: "pipe" },
  );
}
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
async function open(page: Page, name: string) {
  await navigateWorkspace(
    page,
    /^(场站台账|场站信息查询|场站数据同步与场站信息查询)$/,
  );
  await page.getByLabel("搜索场站", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("button", { name: `查看详情 ${name}`, exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "历史与流转", exact: true }).click();
  await expect(
    dialog.getByText("历史电量（独立来源）", { exact: true }),
  ).toBeVisible();
  return dialog;
}

test("exact-station energy shows separate sources, zero and unknown days with scoped association access and narrow keyboard scrolling", async ({
  browser,
}) => {
  const fixture = create();
  const contexts = await Promise.all(
    [0, 1, 2].map(() => browser.newContext({ timezoneId: "America/New_York" })),
  );
  try {
    const [owner, association, outside] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    const dialog = await open(owner, fixture.name);
    await dialog.getByText("历史电量（独立来源）", { exact: true }).click();
    const panel = dialog.getByLabel("单站历史电量", { exact: true });
    await expect(panel).toHaveAttribute("aria-busy", "false");
    await panel
      .getByLabel("单站电量开始日期", { exact: true })
      .fill("2026-11-01");
    await panel
      .getByLabel("单站电量结束日期", { exact: true })
      .fill("2026-11-03");
    await panel
      .getByRole("button", { name: "查询单站电量", exact: true })
      .click();
    await expect(panel).toContainText("已应用日期 2026-11-01 至 2026-11-03");
    const sources = panel.getByLabel("单站电量来源汇总", { exact: true });
    const source = sources.getByRole("row").filter({
      has: owner.getByRole("cell", {
        name: "browser-history-a",
        exact: true,
      }),
    });
    await expect(source).toContainText("0.3");
    await expect(source).toContainText("2 / 1");
    await expect(source).toContainText("3 / 1");
    const daily = panel.getByLabel("逐日电量 browser-history-a", {
      exact: true,
    });
    const zero = daily.getByRole("row").filter({
      has: owner.getByRole("cell", { name: "2026-11-02", exact: true }),
    });
    await expect(zero.getByRole("cell").nth(1)).toHaveText("0");
    const missing = daily.getByRole("row").filter({
      has: owner.getByRole("cell", { name: "2026-11-03", exact: true }),
    });
    await expect(missing.getByRole("cell").nth(1)).toHaveText("暂无观测");
    await panel
      .getByLabel("单站电量来源", { exact: true })
      .fill("unapplied-draft");
    await panel
      .getByLabel("历史电量明细来源", { exact: true })
      .selectOption("browser-history-b");
    await expect(
      panel.getByLabel("逐日电量 browser-history-b", { exact: true }),
    ).toContainText("7.5");
    await expect(panel.getByLabel("单站电量来源", { exact: true })).toHaveValue(
      "unapplied-draft",
    );
    await dialog.screenshot({
      path: "test-results/station-history-energy-desktop.png",
    });
    await owner.setViewportSize({ width: 390, height: 900 });
    await sources.scrollIntoViewIfNeeded();
    await sources.focus();
    await owner.keyboard.press("ArrowRight");
    await expect
      .poll(() => sources.evaluate((el) => el.scrollLeft))
      .toBeGreaterThan(0);
    expect(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
    ).toBe(true);
    await expect(
      dialog.getByRole("button", { name: "关闭详情", exact: true }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: "test-results/station-history-energy-narrow.png",
    });
    await login(association, "association");
    const assoc = await open(association, fixture.name);
    await expect(
      assoc.getByText("接入与流转记录", { exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await association.request.get(
          `/api/v1/stations/${fixture.station_id}/flow`,
        )
      ).status(),
    ).toBe(403);
    await assoc.getByText("历史电量（独立来源）", { exact: true }).click();
    await expect(
      assoc.getByLabel("单站历史电量", { exact: true }),
    ).toHaveAttribute("aria-busy", "false");
    await login(outside, "operator_b");
    expect(
      (
        await outside.request.get(
          `/api/v1/stations/${fixture.station_id}/energy-history?start_date=2026-11-01&end_date=2026-11-03`,
        )
      ).status(),
    ).toBe(404);
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    clean(fixture.station_id);
  }
});

test("station flow keeps distinct clocks and real application/action pages without fabricating reporting state", async ({
  browser,
}) => {
  const fixture = create();
  const contexts = await Promise.all(
    [0, 1].map(() => browser.newContext({ timezoneId: "America/New_York" })),
  );
  try {
    const [owner, regulator] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    const dialog = await open(owner, fixture.name);
    await dialog.getByText("接入与流转记录", { exact: true }).click();
    const flow = dialog.getByLabel("单站接入流转", { exact: true });
    await expect(flow).toContainText("第1页 · 共23条申请");
    await expect(flow).toContainText("08:05:00");
    await expect(flow).toContainText("待确认正式时效规则");
    await expect(flow).toContainText("接入通过");
    await flow
      .getByRole("button", {
        name: `查看流转动作 ${fixture.flow_application_id}`,
        exact: true,
      })
      .click();
    const actions = flow.getByLabel(
      `申请流转动作 ${fixture.flow_application_id}`,
      { exact: true },
    );
    await expect(actions).toContainText("第1页 · 共26条动作");
    await expect(actions).toContainText("审核通过");
    await actions
      .getByRole("button", { name: "流转动作下一页", exact: true })
      .click();
    await expect(actions).toContainText("第2页 · 共26条动作");
    await actions
      .getByRole("button", { name: "流转动作下一页", exact: true })
      .click();
    await expect(
      actions.getByText("演示历史动作0", { exact: true }),
    ).toBeVisible();
    await flow
      .getByRole("button", { name: "场站流转下一页", exact: true })
      .click();
    await expect(flow).toContainText("第2页 · 共23条申请");
    await flow
      .getByRole("button", { name: "场站流转下一页", exact: true })
      .click();
    await expect(flow).toContainText("第3页 · 共23条申请");
    await expect(actions).toContainText("第3页 · 共26条动作");
    await dialog.screenshot({
      path: "test-results/station-history-flow-desktop.png",
    });
    await login(regulator, "regulator");
    const reg = await open(regulator, fixture.name);
    await reg.getByText("接入与流转记录", { exact: true }).click();
    await expect(reg.getByLabel("单站接入流转", { exact: true })).toContainText(
      "第1页 · 共23条申请",
    );
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    clean(fixture.station_id);
  }
});
