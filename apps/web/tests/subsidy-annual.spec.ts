import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { subsidyZip } from "./subsidy-fixture";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("operator_a");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      exact: true,
    }),
  ).toBeVisible();
  await navigateWorkspace(page, "补贴申请管理");
  await navigateWorkspace(page, "新建补贴申请");
}
function fixtures(
  count: number,
  group: string,
): { station_id: number; name: string }[] {
  return JSON.parse(
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
        "--count",
        String(count),
        "--group-name",
        group,
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  ).items;
}
function clean(items: { station_id: number }[]) {
  for (const item of items)
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "clean",
        "--station",
        String(item.station_id),
      ],
      { cwd: "../server" },
    );
}
test("annual multi-station form preserves drafts and recovers a lost success response by the same receipt", async ({
  page,
}) => {
  const group = `annual-${Date.now()}`,
    items = fixtures(2, group);
  try {
    await login(page);
    const panel = page.getByRole("region", {
      name: "年度多场站申报",
      exact: true,
    });
    const yearResponse = await page.request.get(
      "/api/v1/subsidy/annual-stations",
    );
    const defaults = await yearResponse.json();
    await expect(panel.getByLabel("补贴年度", { exact: true })).toHaveValue(
      String(defaults.default_year),
    );
    // Existing demo policy and actual fixture energy are 2026; do not invent prior-year data.
    await panel.getByLabel("补贴年度", { exact: true }).fill("2026");
    await panel
      .getByLabel("年度政策版本", { exact: true })
      .selectOption({ label: "演示按电量补贴（非正式政策） V1" });
    await panel.getByLabel("年度场站名称", { exact: true }).fill(group);
    await panel
      .getByRole("button", { name: "查询年度场站", exact: true })
      .click();
    for (const item of items)
      await panel
        .getByRole("button", { name: `选择年度场站 ${item.name}`, exact: true })
        .click();
    await expect(
      panel.getByText("已选 2 / 20 座", { exact: true }),
    ).toBeVisible();
    await panel
      .getByLabel("年度申请说明", { exact: true })
      .fill("本年度多场站申报说明");
    await panel.getByLabel("年度ZIP材料", { exact: true }).setInputFiles({
      name: "年度材料.zip",
      mimeType: "application/zip",
      buffer: subsidyZip,
    });
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(panel.getByLabel("年度申请说明", { exact: true })).toHaveValue(
      "本年度多场站申报说明",
    );
    await expect(
      panel.getByText("已选 2 / 20 座", { exact: true }),
    ).toBeVisible();
    const requests: string[] = [];
    await page.route("**/api/v1/subsidy/annual-batches", async (route) => {
      requests.push(
        route
          .request()
          .postData()
          ?.match(/"request_key":"([^"]+)"/)?.[1] ?? "",
      );
      if (requests.length === 1) {
        const actual = await route.fetch();
        expect(actual.status()).toBe(201);
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "模拟响应丢失，请用原清单重试" }),
        });
      } else await route.continue();
    });
    await panel
      .getByRole("button", { name: "提交年度申请", exact: true })
      .click();
    const confirmation = panel.getByRole("region", {
      name: "年度申报确认",
      exact: true,
    });
    await expect(confirmation).toContainText("确认2026年度 · 2座场站");
    const pendingUrl = page.url();
    await navigateWorkspace(page, "补贴申请管理");
    await expect(page).toHaveURL(pendingUrl);
    await expect(confirmation).toBeVisible();
    await confirmation
      .getByRole("button", { name: "确认办理年度申请", exact: true })
      .click();
    await expect(panel.getByRole("alert")).toContainText("模拟响应丢失");
    const replayResponse = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/subsidy/annual-batches") &&
        r.status() === 201,
    );
    await confirmation
      .getByRole("button", { name: "确认办理年度申请", exact: true })
      .click();
    const replay = await (await replayResponse).json();
    expect(replay.replayed).toBe(true);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toBeTruthy();
    expect(requests[0]).toBe(requests[1]);
    await expect(panel.getByRole("status")).toContainText(
      "年度申请已核对原回执",
    );
    expect(replay.receipt.items).toHaveLength(2);
    for (const claim of replay.receipt.items) {
      const detail = await (
        await page.request.get(`/api/v1/subsidy/claims/${claim.id}`)
      ).json();
      expect(detail.status).toBe("submitted");
      expect(detail.snapshot.application_year).toBe(2026);
      expect(detail.snapshot.submission.note).toBe("本年度多场站申报说明");
      expect(detail.snapshot.submission.materials).toHaveLength(1);
    }
    await page.screenshot({
      path: "test-results/subsidy-annual-receipt.png",
      fullPage: true,
    });
    await panel
      .getByRole("button", { name: "返回补贴申请管理", exact: true })
      .click();
    await page.getByLabel("补贴申请年度筛选", { exact: true }).fill("2026");
    await page.getByLabel("补贴检索名称", { exact: true }).fill(group);
    const filtered = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/subsidy/claims" &&
        new URL(r.url()).searchParams.get("application_year") === "2026",
    );
    await page
      .getByRole("button", { name: "查询补贴记录", exact: true })
      .click();
    expect(
      (await (await filtered).json()).items.map(
        (item: { application_year: number }) => item.application_year,
      ),
    ).toEqual([2026, 2026]);
  } finally {
    clean(items);
  }
});

test("annual station selection persists across pages, caps at twenty and removes only from selected list", async ({
  page,
}) => {
  const group = `annual-limit-${Date.now()}`,
    items = fixtures(21, group);
  try {
    await login(page);
    const panel = page.getByRole("region", {
      name: "年度多场站申报",
      exact: true,
    });
    await panel.getByLabel("年度场站名称", { exact: true }).fill(group);
    await panel
      .getByRole("button", { name: "查询年度场站", exact: true })
      .click();
    for (const item of items.slice(0, 10))
      await panel
        .getByRole("button", { name: `选择年度场站 ${item.name}`, exact: true })
        .click();
    await panel
      .getByRole("button", { name: "年度场站下一页", exact: true })
      .click();
    for (const item of items.slice(10, 20))
      await panel
        .getByRole("button", { name: `选择年度场站 ${item.name}`, exact: true })
        .click();
    await panel
      .getByRole("button", { name: "年度场站下一页", exact: true })
      .click();
    const last = panel.getByRole("button", {
      name: `选择年度场站 ${items[20].name}`,
      exact: true,
    });
    await expect(last).toBeDisabled();
    await expect(
      panel.getByText("已选 20 / 20 座", { exact: true }),
    ).toBeVisible();
    await panel
      .getByRole("button", {
        name: `移除年度场站 ${items[0].name}`,
        exact: true,
      })
      .click();
    await expect(last).toBeEnabled();
    await last.click();
    await panel
      .getByRole("button", { name: "全部移除年度场站", exact: true })
      .click();
    await expect(
      panel.getByRole("button", { name: "暂存年度草稿", exact: true }),
    ).toBeDisabled();
  } finally {
    clean(items);
  }
});
