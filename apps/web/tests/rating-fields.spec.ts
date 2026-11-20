import { returnToBusinessList } from "./business-workspace";
import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { pickStation } from "./station";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      exact: true,
    }),
  ).toBeVisible();
}
test("indicator configuration versions preserve effective dates, frozen platform fields and mapped scores", async ({
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
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const code = "browser-config-" + randomBytes(8).toString("hex"),
    name = "字段阈值配置 " + code;
  const association = await browser.newContext(),
    operator = await browser.newContext();
  try {
    const ass = await association.newPage(),
      op = await operator.newPage();
    await login(ass, "association");
    await navigateWorkspace(ass, "星级评价");
    await navigateWorkspace(ass, "评价标准管理");
    await ass
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    await ass.getByLabel("标准代码", { exact: true }).fill(code);
    await ass.getByLabel("标准名称", { exact: true }).fill(name);
    const today = await ass
      .getByLabel("指标1生效日期", { exact: true })
      .inputValue();
    await ass.getByLabel("指标2来源", { exact: true }).selectOption("runtime");
    await expect(ass.getByLabel("指标2字段", { exact: true })).toBeEnabled();
    await ass
      .getByLabel("指标2字段", { exact: true })
      .selectOption("equipment_count");
    await expect(ass.getByLabel("2星最低总分", { exact: true })).toBeVisible();
    await expect(ass.getByLabel("4星最低总分", { exact: true })).toBeVisible();
    await ass
      .locator("form.standard-builder")
      .screenshot({ path: "test-results/rating-configured-builder.png" });
    const created = ass.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standards") &&
        r.request().method() === "POST",
    );
    await ass
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const response = await created;
    expect(response.status()).toBe(201);
    const standard = await response.json();
    expect(standard.rules.configuration_version).toBe(2);
    expect(
      standard.rules.thresholds.map((item: { stars: number }) => item.stars),
    ).toEqual([1, 2, 3, 4, 5]);
    expect(standard.effective_on).toBe(today);
    await ass.getByText(`查看指标配置 ${name} V1`, { exact: true }).click();
    const details = ass.locator("details").filter({
      has: ass.getByText(`查看指标配置 ${name} V1`, { exact: true }),
    });
    await expect(details).toContainText("当前库中设备数量");
    await expect(details).toContainText("优秀 → 100分");
    await ass
      .getByRole("button", { name: `基于此版新建 ${code} V2`, exact: true })
      .click();
    await expect(ass.getByLabel("标准代码", { exact: true })).toHaveValue(code);
    await expect(ass.getByLabel("指标2字段", { exact: true })).toHaveValue(
      "equipment_count",
    );
    const future = new Date(today + "T00:00:00Z");
    future.setUTCDate(future.getUTCDate() + 1);
    const tomorrow = future.toISOString().slice(0, 10);
    await ass.getByLabel("指标1生效日期", { exact: true }).fill(tomorrow);
    const saved = ass.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standards") &&
        r.request().method() === "POST",
    );
    await ass
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const scheduled = await (await saved).json();
    expect(scheduled.activation_state).toBe("scheduled");
    await login(op, "operator_a");
    await navigateWorkspace(op, "星级申报");
    await navigateWorkspace(op, "新增星级申报");
    await expect(
      op
        .getByLabel("评星标准", { exact: true })
        .locator(`option[value="${scheduled.id}"]`),
    ).toBeDisabled();
    await op
      .getByLabel("评星标准", { exact: true })
      .selectOption(String(standard.id));
    await pickStation(op, "评星场站", fixture);
    await op.getByLabel("自评-service", { exact: true }).fill("95");
    await expect(op.getByLabel("自评-safety", { exact: true })).toHaveCount(0);
    await op
      .getByLabel("评星依据说明", { exact: true })
      .fill("根据冻结台账与人工材料原值申报");
    const application = op.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/applications") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "保存评星草稿", exact: true }).click();
    const createdApplication = await (await application).json();
    const path = `/api/v1/ratings/applications/${createdApplication.id}`;
    const original = await (await op.request.get(path)).json();
    expect(original.calculation.total).toBe("76.00");
    expect(original.calculation.stars).toBe(2);
    expect(
      original.rule_snapshot.evaluation_context.fields.equipment_count,
    ).toBe("1");
    await expect(
      op.getByText(
        /服务材料 · 字段专家\/材料评分原值 · 原值 95 → 优秀 · 得分 100/,
      ),
    ).toBeVisible();
    await expect(
      op.getByText(/安全材料 · 字段当前库中设备数量 · 原值 1 → 一般 · 得分 40/),
    ).toBeVisible();
    const ownerIdentity = await (
      await op.request.get("/api/v1/auth/me")
    ).json();
    expect(
      (
        await op.request.post(path + "/actions", {
          headers: { "x-csrf-token": ownerIdentity.csrf_token },
          data: { action: "submit", version: 1 },
        })
      ).status(),
    ).toBe(200);
    await ass.getByRole("button", { name: "刷新数据", exact: true }).click();
    await returnToBusinessList(ass, "星级");
    await ass
      .getByLabel("星级检索编号", { exact: true })
      .fill(String(createdApplication.id));
    await ass
      .getByRole("button", { name: "查询星级记录", exact: true })
      .click();
    await ass
      .getByRole("row")
      .filter({ hasText: `RATE-${createdApplication.id}` })
      .getByRole("button", { name: "评星详情", exact: true })
      .click();
    await expect(ass.getByLabel("复核-service", { exact: true })).toBeVisible();
    await expect(ass.getByLabel("复核-safety", { exact: true })).toHaveCount(0);
    const identity = await (await ass.request.get("/api/v1/auth/me")).json();
    const action = async (action: string, version: number, extra = {}) =>
      ass.request.post(path + "/actions", {
        headers: { "x-csrf-token": identity.csrf_token },
        data: { action, version, comment: "核对冻结字段与分档", ...extra },
      });
    expect(
      (await action("review", 2, { scores: { safety: "100" } })).status(),
    ).toBe(422);
    expect(
      (await action("review", 2, { scores: { service: "95" } })).status(),
    ).toBe(200);
    const valid = new Date(today + "T00:00:00Z");
    valid.setUTCDate(valid.getUTCDate() + 30);
    expect(
      (
        await action("publish", 3, {
          valid_until: valid.toISOString().slice(0, 10),
        })
      ).status(),
    ).toBe(200);
    const report = await ass.request.get(path + "/report");
    expect(report.status()).toBe(200);
    const html = await report.text();
    expect(html).toContain("复核字段原值");
    expect(html).toContain("映射档位");
    expect(html).toContain("equipment_count");
    expect(html).toContain("76.00");
    await ass.getByRole("button", { name: "刷新数据", exact: true }).click();
    await returnToBusinessList(ass, "星级");
    await ass
      .getByLabel("星级检索编号", { exact: true })
      .fill(String(createdApplication.id));
    await ass
      .getByRole("button", { name: "查询星级记录", exact: true })
      .click();
    await ass
      .getByRole("row")
      .filter({ hasText: `RATE-${createdApplication.id}` })
      .getByRole("button", { name: "评星详情", exact: true })
      .click();
    await expect(ass.getByLabel("复核-safety", { exact: true })).toHaveCount(0);
    const after = await (await op.request.get(path)).json();
    expect(after.rule_snapshot).toEqual(original.rule_snapshot);
    await ass.screenshot({
      path: "test-results/rating-configured-evidence.png",
      fullPage: true,
    });
  } finally {
    await association.close();
    await operator.close();
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
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.rating_standard_fixture", "--code", code],
      { cwd: "../server" },
    );
  }
});
