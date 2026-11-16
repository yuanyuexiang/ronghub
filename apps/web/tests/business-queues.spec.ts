import { pickStation } from "./station";
import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("subsidy and rating queues reach historical details and filter exact records", async ({
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
        "--operating",
        "--business-history",
        "25",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    for (const [label, menu, ids, prefix, detailButton] of [
      ["补贴", "补贴申报", fixture.claim_ids, "SUB", "补贴详情"],
      ["星级", "星级申报", fixture.rating_ids, "RATE", "评星详情"],
    ] as const) {
      await page.getByRole("button", { name: menu, exact: true }).click();
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      await page.getByLabel(`${label}筛选状态`).selectOption("withdrawn");
      if (label === "星级")
        await page.getByLabel("星级筛选类型").selectOption("initial");
      await expect(
        page.getByRole("button", { name: `查询${label}记录`, exact: true }),
      ).toBeEnabled();
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共25条`, { exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: `${label}下一页`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第2页 · 共25条`, { exact: true }),
      ).toBeVisible();
      // A draft edit must not change the export's already-applied query.
      await page.getByLabel(`${label}检索名称`).fill("unapplied-search-draft");
      const downloadEvent = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "导出申请清单", exact: true })
        .click();
      const downloaded = await downloadEvent;
      const downloadedPath = await downloaded.path();
      expect(downloadedPath).toBeTruthy();
      const csv = readFileSync(downloadedPath!, "utf8");
      expect(csv.trim().split(/\r?\n/)).toHaveLength(26);
      expect(csv).toContain(fixture.name);
      expect(csv).toContain(String(ids[0]));
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      const modulePath =
        label === "补贴" ? "subsidy/claims" : "ratings/applications";
      const errorRoute = `**/api/v1/${modulePath}/process/export?*`;
      await page.route(errorRoute, (route) =>
        route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "单次最多导出10000条，请缩小查询范围",
          }),
        }),
      );
      await page
        .getByRole("button", { name: "导出办理过程", exact: true })
        .click();
      await expect(
        page.locator(".business-export-controls").getByRole("alert"),
      ).toHaveText("单次最多导出10000条，请缩小查询范围");
      await page.unroute(errorRoute);
      const processEvent = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "导出办理过程", exact: true })
        .click();
      const processDownload = await processEvent;
      expect(readFileSync((await processDownload.path())!, "utf8")).toContain(
        "历史快照状态",
      );
      if (label === "星级")
        await page.screenshot({
          path: "test-results/business-exports.png",
          fullPage: true,
        });
      const oldest = ids[0];
      await page
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", {
            name: `${prefix}-${oldest}`,
            exact: true,
          }),
        })
        .getByRole("button", { name: detailButton, exact: true })
        .click();
      await expect(
        page.getByRole("heading", {
          name: new RegExp(`^${prefix}-${oldest} ·`),
        }),
      ).toBeVisible();
      await page.getByLabel(`${label}检索编号`).fill(String(oldest));
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共1条`, { exact: true }),
      ).toBeVisible();
      await page.getByLabel(`${label}筛选状态`).selectOption("submitted");
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`当前条件下没有${label}记录`, { exact: true }),
      ).toBeVisible();
      await page.getByLabel(`${label}筛选状态`).selectOption("withdrawn");
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共1条`, { exact: true }),
      ).toBeVisible();
      await pickStation(
        page,
        label === "补贴" ? "补贴场站" : "评星场站",
        fixture,
      );
      if (label === "补贴") {
        await page
          .getByLabel("补贴政策")
          .selectOption({ label: "演示按电量补贴（非正式政策） V1" });
        await page.getByLabel("补贴开始日期").fill("2026-11-01");
        await page.getByLabel("补贴结束日期").fill("2026-11-30");
      } else {
        await page
          .getByLabel("评星标准")
          .selectOption({ label: "演示星级标准（非正式标准） V1" });
        await page.getByLabel("自评-service", { exact: true }).fill("90");
        await page.getByLabel("自评-safety", { exact: true }).fill("90");
        await page.getByLabel("评星依据说明").fill("队列筛选下创建演示草稿");
      }
      const endpoint =
        label === "补贴"
          ? "/api/v1/subsidy/claims"
          : "/api/v1/ratings/applications";
      const created = page.waitForResponse(
        (response) =>
          response.url().endsWith(endpoint) &&
          response.request().method() === "POST",
      );
      await page
        .getByRole("button", {
          name: label === "补贴" ? "核算并创建草稿" : "保存评星草稿",
          exact: true,
        })
        .click();
      const response = await created;
      expect(response.status()).toBe(201);
      const record = await response.json();
      await expect(page.getByLabel(`${label}筛选状态`)).toHaveValue("");
      await expect(page.getByLabel(`${label}检索编号`)).toHaveValue("");
      await expect(page.getByLabel(`${label}检索名称`)).toHaveValue("");
      await expect(
        page.getByRole("cell", { name: `${prefix}-${record.id}`, exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: `test-results/${label === "补贴" ? "subsidy" : "rating"}-queue.png`,
        fullPage: true,
      });
    }
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
