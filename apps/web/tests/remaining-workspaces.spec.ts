import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { pickStation } from "./station";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, role = "regulator") {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(role);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("separate roles and accounts protect drafts during browser Back", async ({
  page,
}) => {
  await login(page);
  await navigateWorkspace(page, "账号管理");
  await expect(page).toHaveURL(/module=/);
  await expect(
    page.getByRole("heading", { name: "账号管理", exact: true, level: 2 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "业务权限角色", exact: true }),
  ).toBeHidden();
  await expect(page.locator(".authorization-workspace")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await page.screenshot({
    path: "../../.backups/batch567-current-accounts.png",
    fullPage: true,
  });
  await navigateWorkspace(page, "角色管理");
  await page.getByRole("button", { name: "新建权限角色", exact: true }).click();
  await page.getByLabel("权限角色名称").fill("未保存的岗位");
  await page.screenshot({
    path: "../../.backups/batch567-current-role-editor.png",
    fullPage: true,
  });
  page.once("dialog", (dialog) => dialog.dismiss());
  await navigateWorkspace(page, "账号管理");
  await expect(page.getByLabel("权限角色名称")).toHaveValue("未保存的岗位");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.evaluate(() => window.history.back());
  await expect(page.getByLabel("权限角色名称")).toHaveValue("未保存的岗位");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "取消角色编辑", exact: true }).click();
  await navigateWorkspace(page, "账号管理");
  await page.getByRole("button", { name: "新建账号", exact: true }).click();
  await expect(page.getByLabel("搜索账号")).toBeHidden();
  await page.getByRole("button", { name: "返回账号列表", exact: true }).click();
  await expect(page.getByLabel("搜索账号")).toBeVisible();
});
test("construction statistics request actual server dimensions", async ({
  page,
}) => {
  await login(page);
  await navigateWorkspace(page, /^(统计分析|充电量统计)$/);
  await expect(
    page.getByRole("heading", { name: "观测电量同比与环比", exact: true }),
  ).toBeVisible();
  const response = page.waitForResponse(
    (r) =>
      r.url().includes("/api/v1/statistics?") &&
      r.url().includes("dimension=district"),
  );
  await navigateWorkspace(page, "建站统计（区域）");
  expect((await response).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "区域建站统计", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "观测电量同比与环比", exact: true }),
  ).toBeHidden();
  const operator = page.waitForResponse(
    (r) =>
      r.url().includes("/api/v1/statistics?") &&
      r.url().includes("dimension=operator"),
  );
  await navigateWorkspace(page, "建站统计（运营商）");
  expect((await operator).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "运营商建站统计", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../.backups/batch567-current-statistics.png",
    fullPage: true,
  });
  await navigateWorkspace(page, /^(观测电量分析|充电量统计)$/);
  await expect(
    page.getByRole("heading", { name: "观测电量同比与环比", exact: true }),
  ).toBeVisible();
});
test("module URLs restore authorized routes with Back and Forward", async ({
  page,
}) => {
  await login(page);
  await navigateWorkspace(page, "监管协议");
  const saved = page.url();
  await navigateWorkspace(page, /^(统计分析|充电量统计)$/);
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "监管协议与接入说明", exact: true }),
  ).toBeVisible();
  await page.goForward();
  await expect(
    page.getByRole("heading", { name: "统计范围", exact: true }),
  ).toBeVisible();
  await page.goto(saved);
  await expect(
    page.getByRole("heading", { name: "监管协议与接入说明", exact: true }),
  ).toBeVisible();
});
test("content editor hides the list and protects unsaved text", async ({
  page,
}) => {
  await login(page);
  await navigateWorkspace(page, /^(公告与通知|新增发布资讯)$/);
  await page.getByRole("button", { name: "新建内容草稿", exact: true }).click();
  await page.getByLabel("内容标题", { exact: true }).fill("未提交的内容");
  await expect(
    page.getByRole("heading", { name: "发布内容台账", exact: true }),
  ).toBeHidden();
  await expect(
    page.getByRole("toolbar", { name: "正文格式工具", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../.backups/batch567-current-content-editor.png",
    fullPage: true,
  });
  page.once("dialog", (dialog) => dialog.dismiss());
  await navigateWorkspace(page, "监管协议");
  await expect(page.getByLabel("内容标题", { exact: true })).toHaveValue(
    "未提交的内容",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "取消正文编辑", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "发布内容台账", exact: true }),
  ).toBeVisible();
});

test("public role entries retain presentation context without authorizing a role", async ({
  page,
}) => {
  await page.goto("/portal/login");
  for (const [role, name] of [
    ["regulator", "进入监管端登录"],
    ["association", "进入协会端登录"],
    ["operator", "进入运营商端登录"],
  ]) {
    await expect(
      page.getByRole("link", { name: new RegExp(name) }),
    ).toHaveAttribute("href", `/?entry=${role}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../../.backups/batch567-current-public-entry-390.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: /进入协会端登录/ }).click();
  await expect(
    page.getByText("协会端登录 · 登录后按账号实际授权进入工作台", {
      exact: true,
    }),
  ).toBeVisible();
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
});
test("association evaluation process and data entries respect its business boundary", async ({
  page,
}) => {
  await login(page, "association");
  await expect(
    page.getByRole("button", { name: "补贴申请记录", exact: true }),
  ).toHaveCount(0);
  await navigateWorkspace(page, "星级评价");
  await navigateWorkspace(page, /^(评审过程管理|评审过程查看)$/);
  await expect(
    page.getByRole("heading", {
      name: /^(评审过程管理|评审过程查看)$/,
      level: 2,
      exact: true,
    }),
  ).toBeVisible();
  await navigateWorkspace(page, /^(数据支撑评价|数据支撑查看)$/);
  await expect(
    page.getByRole("heading", {
      name: /^(数据支撑评价|数据支撑查看)$/,
      level: 2,
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../.backups/batch567-current-association.png",
    fullPage: true,
  });
  await page.goto("/?module=" + encodeURIComponent("权限与账号"));
  await expect(
    page.getByRole("heading", { name: /^(运营总览|接入汇总)$/, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "账号管理", exact: true, level: 2 }),
  ).toHaveCount(0);
});
test("operator commissioning and decommissioning views apply server kind filters", async ({
  page,
}) => {
  await login(page, "operator_a");
  await navigateWorkspace(page, "场站申请");
  for (const [kind, label] of [
    ["commission", "投运申请"],
    ["decommission", "核销申请"],
  ]) {
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/applications?") &&
        new URL(r.url()).searchParams.get("kind") === kind,
    );
    await page.getByRole("button", { name: label, exact: true }).click();
    expect((await response).status()).toBe(200);
    await expect(page.getByLabel("筛选申请类型", { exact: true })).toHaveValue(
      kind,
    );
    await expect(page.getByLabel("申请类型", { exact: true })).toHaveValue(
      kind,
    );
  }
  await page.screenshot({
    path: "../../.backups/batch567-current-operator.png",
    fullPage: true,
  });
});

test("association data support queries exact-station history and retains zero versus missing", async ({
  page,
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
        "--station-history",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await login(page, "association");
    await navigateWorkspace(page, "星级评价");
    await navigateWorkspace(page, /^(数据支撑评价|数据支撑查看)$/);
    await pickStation(page, "评价数据场站", fixture);
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname ===
        `/api/v1/stations/${fixture.station_id}/energy-history`,
    );
    await page
      .getByRole("button", { name: "查询评价支撑数据", exact: true })
      .click();
    expect((await response).status()).toBe(200);
    const panel = page.getByRole("region", {
      name: "评价数据支撑工作区",
      exact: true,
    });
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
    const daily = panel.getByLabel("逐日电量 browser-history-a", {
      exact: true,
    });
    await expect(
      daily
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", { name: "2026-11-02", exact: true }),
        })
        .getByRole("cell")
        .nth(1),
    ).toHaveText("0");
    await expect(
      daily
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", { name: "2026-11-03", exact: true }),
        })
        .getByRole("cell")
        .nth(1),
    ).toHaveText("暂无观测");
    await page.screenshot({
      path: "../../.backups/batch567-current-rating-data.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(panel).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: "../../.backups/batch567-current-rating-data-390.png",
      fullPage: true,
    });
  } finally {
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
      { cwd: "../server", stdio: "pipe" },
    );
  }
});

test("long content detail keeps its focused heading below the sticky header", async ({
  page,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await login(page);
    const me = await (await page.request.get("/api/v1/auth/me")).json();
    const article = await page.request.post("/api/v1/content", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        kind: "announcement",
        title: fixture.name,
        body: Array(100)
          .fill("长正文用于验证详情标题定位与返回焦点")
          .join("\n"),
        audience: "all",
      },
    });
    expect(article.status()).toBe(201);
    await navigateWorkspace(page, /^(公告与通知|新增发布资讯)$/);
    await page.getByLabel("搜索内容标题", { exact: true }).fill(fixture.name);
    await page.getByRole("button", { name: "查询内容", exact: true }).click();
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: fixture.name, exact: true }),
      })
      .getByRole("button", { name: "查看内容", exact: true })
      .click();
    const heading = page
      .locator(".record-detail")
      .getByRole("heading", { name: fixture.name, exact: true });
    await expect(heading).toBeFocused();
    const bounds = await heading.boundingBox();
    const header = await page.locator(".workspace > header").boundingBox();
    expect(bounds!.y).toBeGreaterThanOrEqual(header!.y + header!.height);
    await page.screenshot({
      path: "../../.backups/batch567-current-long-content.png",
      fullPage: false,
    });
    await page
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "发布内容台账", exact: true }),
    ).toBeFocused();
  } finally {
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
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
