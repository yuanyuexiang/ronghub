import { navigateWorkspace, test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pickStation } from "./station";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("application queue reaches history with combined filters and resets after creation", async ({
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
        "--application-history",
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
    await navigateWorkspace(page, /^(场站申请|接入管理)$/);
    await page.getByLabel("检索申请场站或企业").fill(fixture.name);
    await page.getByLabel("筛选申请类型").selectOption("access");
    await page.getByLabel("筛选申请状态").selectOption("withdrawn");
    await expect(
      page.getByRole("button", { name: "查询申请", exact: true }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await expect(
      page.getByText("申请第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "申请下一页", exact: true }).click();
    await expect(
      page.getByText("申请第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    const oldest = fixture.application_ids[0];
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: `APP-${oldest}`, exact: true }),
      })
      .getByRole("button", { name: "查看处理" })
      .click();
    await expect(
      page.getByRole("heading", {
        name: `APP-${oldest} · 场站接入`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByText("无申请说明", { exact: true })).toBeVisible();
    await page
      .getByRole("button", { name: "← 返回申请列表", exact: true })
      .click();
    await expect(
      page.getByText("申请第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("检索申请编号").fill(String(oldest));
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await expect(
      page.getByText("申请第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: `APP-${oldest} · 场站接入`,
        exact: true,
      }),
    ).toHaveCount(0);
    await page.getByLabel("筛选申请状态").selectOption("submitted");
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await expect(
      page.getByText("当前条件下没有申请记录", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "新增接入申请", exact: true })
      .click();
    await pickStation(page, "申请场站", fixture);
    await page.route(
      /\/api\/v1\/applications\/\d+$/,
      (route) =>
        route.fulfill({ status: 503, json: { detail: "暂时无法读取详情" } }),
      { times: 1 },
    );
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/applications") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "新建草稿", exact: true }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const record = await response.json();
    await expect(page.getByRole("main").getByRole("alert")).toContainText(
      `草稿 APP-${record.id} 已创建`,
    );
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: `APP-${record.id}`, exact: true }),
      })
      .getByRole("button", { name: "查看处理", exact: true })
      .click();
    await expect(
      page.getByRole("heading", {
        name: `APP-${record.id} · 场站接入`,
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: /返回申请列表/ }).click();
    await expect(
      page.getByRole("cell", { name: `APP-${record.id}`, exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("筛选申请状态")).toHaveValue("");
    await expect(page.getByLabel("检索申请编号")).toHaveValue("");
    await expect(page.getByLabel("检索申请场站或企业")).toHaveValue("");
    await page.getByLabel("检索申请场站或企业").fill(fixture.name);
    await page.getByLabel("筛选申请状态").selectOption("draft");
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await expect(
      page.getByText("申请第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/application-queue.png",
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
