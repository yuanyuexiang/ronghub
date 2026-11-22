import { randomBytes } from "node:crypto";
import { navigateWorkspace, test, expect } from "./fixtures";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("station ledger and application picker reach records beyond the first page", async ({
  page,
}) => {
  const group = "pagination-" + randomBytes(6).toString("hex");
  const fixtures = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--count",
        "25",
        "--group-name",
        group,
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  ).items as { station_id: number; name: string }[];
  const target = fixtures.at(-1)!;
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await expect(
      page.getByRole("heading", {
        name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      }),
    ).toBeVisible();
    await navigateWorkspace(
      page,
      /^(场站台账|场站信息查询|场站数据同步与场站信息查询)$/,
    );
    await page.getByLabel("搜索场站", { exact: true }).fill(group);
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect(
      page.getByText("第1页 · 共25个场站", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "场站下一页", exact: true }).click();
    await expect(page.getByText(target.name, { exact: true })).toBeVisible();
    await page.getByLabel("搜索场站", { exact: true }).fill(target.name);
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect(
      page.getByText("第1页 · 共1个场站", { exact: true }),
    ).toBeVisible();
    await navigateWorkspace(page, /^(场站申请|接入管理)$/);
    await page
      .getByRole("button", { name: "新增接入申请", exact: true })
      .click();
    await page.getByLabel("申请场站名称筛选", { exact: true }).fill(group);
    await page
      .locator(".station-picker")
      .filter({ has: page.getByLabel("申请场站", { exact: true }) })
      .getByRole("button", { name: "筛选", exact: true })
      .click();
    await page
      .getByRole("button", { name: "申请场站下一页", exact: true })
      .click();
    await page
      .getByLabel("申请场站", { exact: true })
      .selectOption(String(target.station_id));
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/applications") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "新建草稿", exact: true }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    expect((await response.json()).station_id).toBe(target.station_id);
    await page.screenshot({
      path: "test-results/paged-station-picker.png",
      fullPage: true,
    });
  } finally {
    for (const fixture of fixtures)
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
