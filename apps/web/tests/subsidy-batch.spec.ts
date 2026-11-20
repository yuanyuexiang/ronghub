import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
const batchPath = "/api/v1/subsidy/claims/batch-actions";
function fixture(count: number) {
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
        "--subsidy-pending",
        String(count),
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
}
function clean(station: number) {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "app.browser_fixture",
      "clean",
      "--station",
      String(station),
    ],
    { cwd: "../server", stdio: "pipe" },
  );
}
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  expect((await done).status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "运营总览", exact: true }),
  ).toBeVisible();
}
async function query(page: Page, name: string) {
  await page.getByLabel("补贴检索名称", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询补贴记录", exact: true }).click();
}
const box = (page: Page, id: number) =>
  page.getByRole("checkbox", { name: `选择补贴 SUB-${id}`, exact: true });
const button = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true });
const batchResponse = (page: Page) =>
  page.waitForResponse(
    (r) => r.url().endsWith(batchPath) && r.request().method() === "POST",
  );
async function detail(page: Page, id: number) {
  return (await page.request.get(`/api/v1/subsidy/claims/${id}`)).json();
}

test("batch review freezes cross-page versions, rolls back conflicts and supports retry and rejection", async ({
  browser,
}) => {
  const data = fixture(26),
    context = await browser.newContext(),
    operatorContext = await browser.newContext();
  try {
    const page = await context.newPage();
    await login(page, "regulator");
    await button(page, "补贴管理").click();
    await query(page, data.name);
    await expect(
      page.getByText("补贴第1页 · 共26条", { exact: true }),
    ).toBeVisible();
    const actionBounds = await button(page, "补贴详情").first().boundingBox();
    expect(actionBounds).not.toBeNull();
    expect(actionBounds!.x + actionBounds!.width).toBeLessThanOrEqual(
      page.viewportSize()!.width,
    );
    const newest = data.claim_ids[25],
      oldest = data.claim_ids[0];
    await box(page, newest).check();
    await button(page, "补贴下一页").click();
    await box(page, oldest).check();
    const batch = page.getByRole("region", {
      name: "补贴批量审核",
      exact: true,
    });
    await expect(batch).toContainText("已选2/100条待审补贴");
    await page
      .getByLabel("补贴检索名称", { exact: true })
      .fill("未应用的筛选词");
    await page
      .getByLabel("批量补贴审核意见", { exact: true })
      .fill("已逐条核验材料和金额");
    const posts: string[] = [];
    page.on("request", (r) => {
      if (r.url().endsWith(batchPath) && r.method() === "POST")
        posts.push(r.postData() || "");
    });
    await button(page, "批量通过补贴审核").click();
    const confirmation = page.getByRole("region", {
      name: "补贴批量审核确认",
      exact: true,
    });
    await expect(confirmation).toContainText(`SUB-${newest} · V2`);
    await expect(confirmation).toContainText(`SUB-${oldest} · V2`);
    expect(posts).toHaveLength(0);
    await page.getByLabel("补贴检索名称", { exact: true }).press("Enter");
    await expect(confirmation).toBeVisible();
    await expect(batch).toContainText("已选2/100条待审补贴");
    const csrf = (await (await page.request.get("/api/v1/auth/me")).json())
      .csrf_token;
    const adjustment = await page.request.post(
      `/api/v1/subsidy/claims/${newest}/actions`,
      {
        headers: { "x-csrf-token": csrf },
        data: {
          action: "adjust",
          version: 2,
          delta: "0.01",
          comment: "另一窗口登记调整",
        },
      },
    );
    expect(adjustment.status()).toBe(200);
    await button(page, "刷新数据").click();
    await expect(confirmation).toContainText(`SUB-${newest} · V2`);
    await expect(page.getByLabel("补贴检索名称", { exact: true })).toHaveValue(
      "未应用的筛选词",
    );
    const conflict = batchResponse(page);
    await button(page, "确认通过所选补贴").click();
    expect((await conflict).status()).toBe(409);
    await expect(batch.getByRole("alert")).toContainText("整批未提交");
    const oldDetail = await detail(page, oldest);
    expect(oldDetail.status).toBe("submitted");
    expect(oldDetail.version).toBe(2);
    await button(page, "取消批量审核").click();
    await button(page, "清空补贴选择").click();
    await query(page, data.name);
    await box(page, newest).check();
    await button(page, "补贴下一页").click();
    await box(page, oldest).check();
    await button(page, "批量通过补贴审核").click();
    await expect(confirmation).toContainText(`SUB-${newest} · V3`);
    await expect(confirmation).toContainText("1.02");
    await page.screenshot({
      path: "test-results/subsidy-batch-confirmation.png",
      fullPage: true,
    });
    await page.route("**" + batchPath, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模拟服务暂不可用" }),
      }),
    );
    await button(page, "确认通过所选补贴").click();
    await expect(batch.getByRole("alert")).toContainText("模拟服务暂不可用");
    await expect(confirmation).toBeVisible();
    await page.unroute("**" + batchPath);
    const approved = batchResponse(page);
    await button(page, "确认通过所选补贴").click();
    const response = await approved;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({
      action: "approve",
      comment: "已逐条核验材料和金额",
      items: [
        { id: oldest, version: 2 },
        { id: newest, version: 3 },
      ],
    });
    await expect(batch.getByRole("status")).toContainText("已批量通过2条");
    await expect(batch).toContainText("已选0/100条");
    expect((await detail(page, newest)).adjusted_amount).toBe("1.02");
    await box(page, data.claim_ids[1]).check();
    await page
      .getByLabel("批量补贴审核意见", { exact: true })
      .fill("材料需补充，请重新申报");
    await button(page, "批量驳回补贴申请").click();
    const rejected = batchResponse(page);
    await button(page, "确认驳回所选补贴").click();
    expect((await rejected).status()).toBe(200);
    await expect(batch.getByRole("status")).toContainText("已批量驳回1条");
    const rejectedDetail = await detail(page, data.claim_ids[1]);
    expect(rejectedDetail.status).toBe("rejected");
    expect(rejectedDetail.history.at(-1).comment).toBe(
      "材料需补充，请重新申报",
    );
    const op = await operatorContext.newPage();
    await login(op, "operator_a");
    await button(op, "补贴申报").click();
    await expect(
      op.getByRole("region", { name: "补贴批量审核", exact: true }),
    ).toHaveCount(0);
    const opCsrf = (await (await op.request.get("/api/v1/auth/me")).json())
      .csrf_token;
    expect(
      (
        await op.request.post(batchPath, {
          headers: { "x-csrf-token": opCsrf },
          data: {
            action: "approve",
            comment: "越权",
            items: [{ id: oldest, version: 3 }],
          },
        })
      ).status(),
    ).toBe(403);
  } finally {
    await Promise.allSettled([context.close(), operatorContext.close()]);
    clean(data.station_id);
  }
});

test("batch selection caps at one hundred and query reset clears cross-page selection", async ({
  page,
}) => {
  const data = fixture(101);
  try {
    await login(page, "regulator");
    await button(page, "补贴管理").click();
    await query(page, data.name);
    const batch = page.getByRole("region", {
      name: "补贴批量审核",
      exact: true,
    });
    for (let index = 1; index <= 5; index++) {
      await expect(
        page.getByText(`补贴第${index}页 · 共101条`, { exact: true }),
      ).toBeVisible();
      await button(page, "选择本页待审补贴").click();
      await expect(batch).toContainText(`已选${index * 20}/100条`);
      await button(page, "补贴下一页").click();
    }
    await expect(box(page, data.claim_ids[0])).toBeDisabled();
    await button(page, "选择本页待审补贴").click();
    await expect(
      page.getByText("本页选择会超过100条，请先取消部分选择", { exact: true }),
    ).toBeVisible();
    await expect(batch).toContainText("已选100/100条");
    await button(page, "重置补贴筛选").click();
    await expect(batch).toContainText("已选0/100条");
  } finally {
    clean(data.station_id);
  }
});
