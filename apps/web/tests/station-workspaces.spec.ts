import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture() {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--located",
        "--facilities",
        "--device-inventory",
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
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}

test("station summary and directory remain distinct, export applied filters, and preserve device drafts across detail tabs", async ({
  page,
}) => {
  const value = fixture();
  try {
    await login(page, "regulator");
    await page.getByRole("button", { name: "场站汇总", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "场站接入与充电数据汇总",
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("已标注充电站", { exact: true })).toBeVisible();
    const summaryPanel = page.getByRole("region", {
      name: "场站统计汇总",
      exact: true,
    });
    await expect(
      summaryPanel.getByRole("button", { name: "查询场站汇总", exact: true }),
    ).toBeVisible();
    const trend = summaryPanel.getByRole("region", {
      name: "最近12个月日电量趋势",
      exact: true,
    });
    const bounds = await summaryPanel.boundingBox();
    await expect(trend).toBeVisible();
    expect((await trend.boundingBox())!.width).toBeGreaterThan(
      bounds!.width * 0.85,
    );

    await page.screenshot({
      path: "../../.backups/prototype-batch2-summary-current.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "场站数据同步与场站信息查询", exact: true })
      .click();
    const source = await (
      await page.request.get(`/api/v1/stations/${value.station_id}`)
    ).json();
    await page.getByLabel("搜索场站", { exact: true }).fill(value.name);
    await page
      .getByLabel("场站运营商筛选", { exact: true })
      .selectOption(String(source.operator_id));
    await page
      .getByLabel("场站种类筛选", { exact: true })
      .selectOption(source.station_type);
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/map/stations" &&
        new URL(r.url()).searchParams.get("q") === value.name,
    );
    await page.getByRole("button", { name: "查询", exact: true }).click();
    expect(
      new URL((await response).url()).searchParams.get("operator_id"),
    ).toBe(String(source.operator_id));
    await expect(
      page.getByText("第1页 · 共1个场站", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "../../.backups/prototype-batch2-directory-current.png",
      fullPage: true,
    });
    await page
      .getByLabel("搜索场站", { exact: true })
      .fill("尚未应用的场站筛选");
    const downloading = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出场站CSV", exact: true })
      .click();
    const file = await downloading,
      filePath = await file.path();
    if (!filePath) throw new Error("No export file");
    const csv = readFileSync(filePath, "utf8");
    expect(csv).toContain(value.name);
    expect(csv).not.toContain("尚未应用的场站筛选");
    await page
      .getByRole("button", { name: `查看详情 ${value.name}`, exact: true })
      .click();
    const detail = page.getByRole("dialog");
    await expect(detail.getByLabel("场站供电配套")).toBeVisible();
    await expect(detail.getByLabel("场站基本资料")).toContainText("私人共享桩");
    await page.screenshot({
      path: "../../.backups/prototype-batch2-detail-current.png",
      fullPage: true,
    });
    await detail.getByRole("button", { name: "设备信息", exact: true }).click();
    const equipment = detail.getByLabel("场站设备明细", { exact: true });
    await expect(equipment).toContainText("共24个设备");
    await equipment
      .getByLabel("检索场站设备", { exact: true })
      .fill("未查询的设备草稿");
    await detail.getByRole("button", { name: "基本信息", exact: true }).click();
    await detail.getByRole("button", { name: "设备信息", exact: true }).click();
    await expect(
      equipment.getByLabel("检索场站设备", { exact: true }),
    ).toHaveValue("未查询的设备草稿");
    await page.screenshot({
      path: "../../.backups/prototype-batch2-devices-current.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await detail.getByRole("button", { name: "基本信息", exact: true }).click();
    expect(
      await detail.evaluate((node) => node.scrollWidth <= node.clientWidth + 1),
    ).toBe(true);
    await expect(
      detail.getByRole("button", { name: "关闭详情", exact: true }),
    ).toBeInViewport();
    await page.screenshot({
      path: "../../.backups/prototype-batch2-detail-390.png",
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await expect(detail).toHaveCount(0);
    await expect(page.getByLabel("搜索场站", { exact: true })).toHaveValue(
      "尚未应用的场站筛选",
    );
    await expect(
      page.getByText("第1页 · 共1个场站", { exact: true }),
    ).toBeVisible();
  } finally {
    clean(value.station_id);
  }
});

test("application detail returns to its applied list and protects unsaved changes on back and module navigation", async ({
  page,
}) => {
  const value = fixture();
  try {
    await login(page, "operator_a");
    const headers = {
      "x-csrf-token": (await (await page.request.get("/api/v1/auth/me")).json())
        .csrf_token,
    };
    const created = await page.request.post("/api/v1/applications", {
      headers,
      data: {
        station_id: value.station_id,
        kind: "access",
        payload: { note: "原始申请说明" },
      },
    });
    expect(created.status()).toBe(201);
    const application = await created.json();
    await page.getByRole("button", { name: "场站申请", exact: true }).click();
    await page
      .getByLabel("检索申请编号", { exact: true })
      .fill(String(application.id));
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", {
          name: `APP-${application.id}`,
          exact: true,
        }),
      })
      .getByRole("button", { name: "查看处理", exact: true })
      .click();
    const detail = page.getByRole("region", {
      name: "申请详情工作区",
      exact: true,
    });
    await expect(detail).toBeVisible();
    await expect(page.getByLabel("检索申请编号", { exact: true })).toBeHidden();
    await page.getByLabel("申请说明", { exact: true }).fill("未保存的申请内容");
    await page
      .getByRole("button", { name: "← 返回申请列表", exact: true })
      .click();
    await expect(
      page.getByRole("alert").filter({ hasText: "尚未保存" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "继续编辑申请", exact: true })
      .click();
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.getByRole("button", { name: "场站台账", exact: true }).click();
    await expect(detail).toBeVisible();
    await expect(page.getByLabel("申请说明", { exact: true })).toHaveValue(
      "未保存的申请内容",
    );
    await page.screenshot({
      path: "../../.backups/prototype-batch2-application-current.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "← 返回申请列表", exact: true })
      .click();
    await page
      .getByRole("button", { name: "放弃修改并返回申请列表", exact: true })
      .click();
    await expect(detail).toHaveCount(0);
    await expect(page.getByLabel("检索申请编号", { exact: true })).toHaveValue(
      String(application.id),
    );
    expect(
      (
        await (
          await page.request.get(`/api/v1/applications/${application.id}`)
        ).json()
      ).payload.note,
    ).toBe("原始申请说明");
  } finally {
    clean(value.station_id);
  }
});

test("company detail guards unsaved edits and preserves the operator query when returning", async ({
  page,
}) => {
  const value = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.company_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await login(page, "regulator");
    await page
      .getByRole("button", { name: "场站运营商管理", exact: true })
      .click();
    await page.getByLabel("搜索企业名称", { exact: true }).fill(value.name);
    await page.getByRole("button", { name: "查询企业", exact: true }).click();
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: value.name, exact: true }),
      })
      .getByRole("button", { name: "查看企业资料", exact: true })
      .click();
    const detail = page.getByRole("region", {
      name: "企业详情工作区",
      exact: true,
    });
    await expect(detail).toBeVisible();
    await page
      .getByRole("button", { name: "维护企业资料", exact: true })
      .click();
    await page
      .getByLabel("企业名称", { exact: true })
      .fill("尚未保存的企业名称");
    await page
      .getByRole("button", { name: "返回运营商列表", exact: true })
      .click();
    await expect(
      page.getByRole("alert").filter({ hasText: "尚未保存" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "继续编辑企业资料", exact: true })
      .click();
    await expect(page.getByLabel("企业名称", { exact: true })).toHaveValue(
      "尚未保存的企业名称",
    );
    page.once("dialog", (dialog) => dialog.dismiss());
    await page
      .getByRole("button", { name: "场站数据同步与场站信息查询", exact: true })
      .click();
    await expect(detail).toBeVisible();
    await page.screenshot({
      path: "../../.backups/prototype-batch2-company-current.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "返回运营商列表", exact: true })
      .click();
    await page
      .getByRole("button", { name: "放弃修改并返回运营商列表", exact: true })
      .click();
    await expect(page.getByLabel("搜索企业名称", { exact: true })).toHaveValue(
      value.name,
    );
    await expect(
      page.getByRole("cell", { name: value.name, exact: true }),
    ).toBeVisible();
  } finally {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.company_fixture",
        "clean",
        "--company",
        String(value.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
