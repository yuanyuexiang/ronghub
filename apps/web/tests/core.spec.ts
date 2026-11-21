import { pickStation } from "./station";
import { navigateWorkspace, test, expect } from "./fixtures";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("regulator login, station search, durable sync view and logout", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(
    page.getByRole("heading", { name: /^(运营总览|接入汇总|多维度数据筛选)$/ }),
  ).toBeVisible();
  await expect(page.locator(".demo")).toHaveCount(0);
  expect(
    (await (await page.request.get("/api/v1/auth/me")).json()).demo_mode,
  ).toBe(true);
  await expect(page.getByText("演示·鼓楼公共充电站")).toBeVisible();
  await page.getByLabel("搜索场站").fill("不存在的演示场站");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.getByText("当前条件下没有场站数据")).toBeVisible();
  await navigateWorkspace(page, "场站数据同步");
  await expect(
    page.getByRole("heading", { name: "数据同步任务" }),
  ).toBeVisible();
  await expect(
    page.getByLabel("同步运营商").locator("option").first(),
  ).toHaveText(/演示运营商/);
  const attempts: { operator_id: number; request_key: string }[] = [];
  let task: { id: number } | undefined;
  // Commit both requests on the real server, then lose their browser responses.
  await page.route("**/api/v1/sync/tasks", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    const accepted = await response.json();
    if (task) expect(accepted.id).toBe(task.id);
    task = accepted;
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "发起模拟同步" }).click();
  const receipt = page.getByRole("complementary", { name: "同步请求回执" });
  await expect(
    page.getByRole("alert").filter({ hasText: "未收到提交回执" }),
  ).toBeVisible();
  await expect(page.getByLabel("同步运营商")).toBeDisabled();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(receipt).toContainText(attempts[0].request_key);
  await page.getByRole("button", { name: "按原编号重试同步" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "未收到提交回执" }),
  ).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  await page.getByRole("button", { name: "查询原编号任务" }).click();
  await expect(receipt).toHaveCount(0);
  await expect(page.getByLabel("同步运营商")).toBeEnabled();
  expect(task).toBeDefined();
  const taskId = task!.id;
  // Process this scenario's task, preserving the real lease/fencing path without
  // consuming unrelated queued work from concurrent integration checks.
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      "import sys; from app.sync import claim, execute; task=claim(int(sys.argv[1])); assert task is not None and execute(task)",
      String(taskId),
    ],
    {
      cwd: "../server",
      stdio: "pipe",
    },
  );
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(
    page.getByRole("row").filter({ hasText: `SYNC-${taskId}` }),
  ).toContainText("同步完成");
  await page.screenshot({
    path: "test-results/regulator-sync.png",
    fullPage: true,
  });
  await navigateWorkspace(
    page,
    /^(场站台账|场站信息查询|场站数据同步与场站信息查询)$/,
  );
  await page.getByLabel("搜索场站").fill("演示·接口同步场站");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("button", { name: "查看详情 演示·接口同步场站" })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "设备信息", exact: true })
    .click();
  await expect(
    page.getByRole("dialog").getByText("演示直流设备"),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("充电中", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭详情", exact: true }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
});
test("operator only sees own enterprise stations", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("operator_b");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await navigateWorkspace(page, "信息推送");
  await expect(page.getByText("演示·晋安便民充电站")).toBeVisible();
  await expect(page.getByText("演示·鼓楼公共充电站")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "场站数据同步", exact: true }),
  ).toHaveCount(0);
  await page.screenshot({
    path: "test-results/operator-dashboard.png",
    fullPage: true,
  });
});

test("operator material submission and regulator approval across browser contexts", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const operator = await browser.newContext();
  const regulator = await browser.newContext();
  try {
    const op = await operator.newPage(),
      reg = await regulator.newPage();
    for (const [page, name] of [
      [op, "operator_a"],
      [reg, "regulator"],
    ] as const) {
      await page.goto("http://127.0.0.1:3000");
      await page.getByLabel("账号", { exact: true }).fill(name);
      await page.getByLabel("密码", { exact: true }).fill(password);
      await page.getByRole("button", { name: "登录平台" }).click();
      await expect(
        page.getByRole("heading", {
          name: /^(运营总览|接入汇总|多维度数据筛选)$/,
        }),
      ).toBeVisible();
    }
    await navigateWorkspace(op, /^(场站申请|接入管理)$/);
    await pickStation(op, "申请场站", fixture);
    const created = op.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/applications") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "新建草稿" }).click();
    const application = await (await created).json();
    const row = op
      .getByRole("row")
      .filter({ hasText: `APP-${application.id}` });
    await row.getByRole("button", { name: "查看处理" }).click();
    await op
      .getByLabel("申请说明", { exact: true })
      .fill("浏览器验收：补充场站资质说明");
    await op.getByLabel("申请联系人", { exact: true }).fill("浏览器申请联系人");
    await op.getByLabel("申请联系方式", { exact: true }).fill("13800000001");
    const saved = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/v1/applications/${application.id}`) &&
        r.request().method() === "PATCH",
    );
    await op.getByRole("button", { name: "保存申请信息" }).click();
    expect((await saved).status()).toBe(200);
    await expect(
      op.getByText("浏览器验收：补充场站资质说明", { exact: true }),
    ).toBeVisible();

    let releaseUpload!: () => void, arrived!: () => void;
    const holdUpload = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    const uploadArrived = new Promise<void>((resolve) => {
      arrived = resolve;
    });
    const uploadUrl = `**/api/v1/applications/${application.id}/attachments`;
    await op.route(uploadUrl, async (route) => {
      if (route.request().method() === "POST") {
        arrived();
        await holdUpload;
      }
      await route.continue();
    });
    await expect(op.getByLabel("上传申请材料")).toBeEnabled();
    await op.getByLabel("上传申请材料").setInputFiles({
      name: "验收材料.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nBrowser fixture material"),
    });
    try {
      await uploadArrived;
      await expect(
        op.getByRole("button", { name: "提交审核", exact: true }),
      ).toBeDisabled();
      await expect(
        op.getByRole("button", { name: "保存申请信息", exact: true }),
      ).toBeDisabled();
      await expect(
        op.getByRole("button", { name: "关闭详情", exact: true }),
      ).toBeDisabled();
      await expect(op.getByLabel("检索申请编号", { exact: true })).toBeHidden();
      await expect(
        op.getByRole("button", { name: "← 返回申请列表", exact: true }),
      ).toBeDisabled();
      await expect(op.getByLabel("申请说明", { exact: true })).toHaveValue(
        "浏览器验收：补充场站资质说明",
      );
    } finally {
      releaseUpload();
    }
    await expect(op.getByRole("link", { name: "验收材料.pdf" })).toBeVisible();
    await op.unroute(uploadUrl);
    await op.getByLabel("申请说明", { exact: true }).fill("上传后未保存说明");
    const materialRefresh = op.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/applications/${application.id}/attachments`) &&
        response.request().method() === "GET",
    );
    await op.getByRole("button", { name: "刷新数据", exact: true }).click();
    expect((await materialRefresh).status()).toBe(200);
    await expect(op.getByLabel("申请说明", { exact: true })).toHaveValue(
      "上传后未保存说明",
    );
    await expect(
      op.getByRole("link", { name: "验收材料.pdf", exact: true }),
    ).toBeVisible();
    const submit = op.getByRole("button", { name: "提交审核", exact: true });
    await expect(submit).toBeDisabled();
    const category = op.getByRole("combobox", { name: /^材料分类 / });
    await category.selectOption("other");
    await expect(category).toHaveValue("other");
    await expect(op.getByLabel("申请说明", { exact: true })).toHaveValue(
      "上传后未保存说明",
    );
    await expect(submit).toBeDisabled();
    const updated = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/v1/applications/${application.id}`) &&
        r.request().method() === "PATCH",
    );
    await op.getByRole("button", { name: "保存申请信息", exact: true }).click();
    expect((await updated).status()).toBe(200);
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(
      op.getByRole("region", { name: "申请详情工作区", exact: true }),
    ).toContainText("待审核");
    await expect(op.getByLabel("上传申请材料")).toHaveCount(0);
    await navigateWorkspace(reg, /^(场站申请|接入管理)$/);
    const review = reg
      .getByRole("row")
      .filter({ hasText: `APP-${application.id}` });
    await review.getByRole("button", { name: "查看处理" }).click();
    const frozen = reg.getByRole("article", {
      name: "冻结送审内容",
      exact: true,
    });
    await expect(frozen).toContainText("上传后未保存说明");
    await expect(frozen).toContainText("浏览器申请联系人");
    await expect(frozen).toContainText("13800000001");
    await expect(frozen).toContainText("提交前资料变更校验：已通过");
    await expect(
      frozen.getByRole("link", { name: "验收材料.pdf", exact: true }),
    ).toBeVisible();
    const proofDownload = await reg.request.get(
      (await frozen
        .getByRole("link", { name: "验收材料.pdf", exact: true })
        .getAttribute("href")) as string,
    );
    expect(proofDownload.status()).toBe(200);
    expect((await proofDownload.body()).toString()).toBe(
      "%PDF-1.4\nBrowser fixture material",
    );
    await reg.getByLabel("处理意见").fill("演示验收：资料核验通过");
    const approvalDone = reg.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/v1/applications/${application.id}/actions`) &&
        r.request().method() === "POST",
    );
    await reg.getByRole("button", { name: "审核通过", exact: true }).click();
    const approvedResponse = await approvalDone;
    expect(approvedResponse.status()).toBe(200);
    expect((await approvedResponse.json()).status).toBe("approved");
    await expect(
      reg.getByRole("region", { name: "申请详情工作区", exact: true }),
    ).toContainText("已通过");
    await reg.screenshot({
      path: "test-results/regulator-approval.png",
      fullPage: true,
    });
    const station = await reg.request.get(
      `/api/v1/stations/${fixture.station_id}`,
    );
    expect((await station.json()).status).toBe("access_approved");
  } finally {
    await Promise.allSettled([operator.close(), regulator.close()]);
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
