import { returnToBusinessList } from "./business-workspace";
import { subsidyZip } from "./subsidy-fixture";
import { pickStation } from "./station";
import { navigateWorkspace, test, expect, Page } from "./fixtures";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  const loginDone = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台" }).click();
  const response = await loginDone;
  expect(
    response.status(),
    response.status() === 200 ? "" : (await response.json()).detail,
  ).toBe(200);
  await expect(
    page.getByRole("heading", { name: /^(运营总览|接入汇总)$/ }),
  ).toBeVisible();
}
async function verifyMaterialDownload(
  page: Page,
  applicationId: number,
  submitted: number | null,
) {
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载全部材料", exact: true }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(
    `ronghub-rating-${applicationId}-materials.zip`,
  );
  const path = await download.path();
  expect(path).toBeTruthy();
  const proof = JSON.parse(
    execFileSync(
      "../server/.venv/bin/python",
      [
        "-c",
        `
import hashlib, json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as bundle:
    manifest = json.loads(bundle.read("manifest.json"))
    assert len(manifest["materials"]) == 1
    material = manifest["materials"][0]
    content = bundle.read(material["entry"])
    assert content == bytes([0x50, 0x4b, 0x03, 0x04, 0x01])
    assert hashlib.sha256(content).hexdigest() == material["sha256"]
    assert bundle.namelist() == [material["entry"], "manifest.json"]
    print(json.dumps(manifest))
`,
        path!,
      ],
      { encoding: "utf8" },
    ),
  );
  expect(proof.application_id).toBe(applicationId);
  expect(proof.materials[0].submitted_version).toBe(submitted);
}
test("subsidy snapshot approval and association rating publication", async ({
  browser,
}) => {
  // Complete subsidy review, publication/report download, appeal and delisting
  // across three contexts, including owned-fixture cleanup. Per-step deadlines stay unchanged.
  test.setTimeout(60_000);
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext({ timezoneId: "UTC" }),
    browser.newContext({ timezoneId: "UTC" }),
    browser.newContext({ timezoneId: "UTC" }),
  ]);
  try {
    const [op, reg, assoc] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(op, "operator_a");
    await login(reg, "regulator");
    await login(assoc, "association");
    await expect(
      assoc.getByRole("heading", { name: "协会评审工作台" }),
    ).toBeVisible();
    const summary = await (await assoc.request.get("/api/v1/dashboard")).json();
    expect(summary).not.toHaveProperty("subsidy_total");
    expect(summary.pending).toBeNull();
    await navigateWorkspace(op, "补贴申报");
    await pickStation(op, "补贴场站", fixture);
    await op
      .getByLabel("补贴政策", { exact: true })
      .selectOption({ label: "演示按电量补贴（非正式政策） V1" });
    await op
      .getByLabel("补贴申请说明", { exact: true })
      .fill("演示申报说明与核验材料");
    const created = op.waitForResponse(
      (r) =>
        r.url().endsWith("/subsidy/claims") && r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "核算并创建草稿" }).click();
    const response = await created;
    expect(response.request().postDataJSON().station_id).toBe(
      fixture.station_id,
    );
    expect(response.status(), await response.text()).toBe(201);
    const claim = await response.json();
    await expect(
      op.getByRole("button", { name: "关闭补贴详情", exact: true }),
    ).toBeVisible();
    await returnToBusinessList(op, "补贴");
    await op.getByLabel("补贴检索编号", { exact: true }).fill(String(claim.id));
    await op.getByRole("button", { name: "查询补贴记录", exact: true }).click();

    const opRow = op.getByRole("row", { includeHidden: true }).filter({
      has: op.getByRole("cell", {
        name: `SUB-${claim.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await opRow.getByRole("button", { name: "补贴详情" }).click();
    await expect(
      op.getByLabel("上传补贴ZIP材料", { exact: true }),
    ).toBeEnabled();
    await op.getByLabel("上传补贴ZIP材料", { exact: true }).setInputFiles({
      name: "申报资料.zip",
      mimeType: "application/zip",
      buffer: subsidyZip,
    });
    await expect(
      op.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeEnabled();
    await op.getByRole("button", { name: "提交补贴审核" }).click();
    await expect(opRow).toContainText("待审核");
    await navigateWorkspace(reg, "补贴申请记录");
    await reg
      .getByLabel("补贴检索编号", { exact: true })
      .fill(String(claim.id));
    await reg
      .getByRole("button", { name: "查询补贴记录", exact: true })
      .click();

    const regRow = reg.getByRole("row", { includeHidden: true }).filter({
      has: reg.getByRole("cell", {
        name: `SUB-${claim.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await regRow.getByRole("button", { name: "补贴详情" }).click();
    await reg.getByLabel("补贴处理意见").fill("演示核算依据已核验");
    await reg
      .getByRole("button", { name: "通过补贴审核", exact: true })
      .click();
    await expect(regRow).toContainText("已审定");
    const snapshot = await (
      await reg.request.get(`/api/v1/subsidy/claims/${claim.id}`)
    ).json();
    expect(snapshot.base_amount).toBe("4.20");
    await reg.getByLabel("补贴处理意见").fill("演示发放凭证，不触发支付");
    await reg.getByRole("button", { name: "登记发放状态（不付款）" }).click();
    await expect(regRow).toContainText("已登记发放");
    await reg.screenshot({
      path: "test-results/subsidy-review.png",
      fullPage: true,
    });
    await navigateWorkspace(op, "星级申报");
    await pickStation(op, "评星场站", fixture);
    await op.getByLabel("自评-service").fill("90");
    await op.getByLabel("自评-safety").fill("80");
    await op.getByLabel("评星依据说明").fill("演示现场核查记录");
    const ratingCreated = op.waitForResponse(
      (r) =>
        r.url().endsWith("/ratings/applications") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "保存评星草稿" }).click();
    const ratingResponse = await ratingCreated;
    expect(ratingResponse.status()).toBe(201);
    const rating = await ratingResponse.json();
    const ratingInfo = op.getByTestId("rating-submission-info");
    await expect(ratingInfo).toContainText(fixture.name);
    await expect(ratingInfo).toContainText("演示运营商甲");
    await expect(ratingInfo).toContainText("无提交账号记录");
    await expect(ratingInfo).toContainText("无提交时间记录");
    await expect(ratingInfo).toContainText("V1");

    await op
      .getByLabel("评星材料分类", { exact: true })
      .selectOption("site_plan");
    const materialSaved = op.waitForResponse(
      (r) =>
        r.url().endsWith(`/ratings/applications/${rating.id}/attachments`) &&
        r.request().method() === "POST",
    );
    await op.getByLabel("上传评星材料", { exact: true }).setInputFiles({
      name: "site-plan.zip",
      mimeType: "application/zip",
      buffer: Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x01]),
    });
    expect((await materialSaved).status()).toBe(201);
    await expect(op.getByRole("link", { name: "site-plan.zip" })).toBeVisible();
    const archiveUrl = `**/api/v1/ratings/applications/${rating.id}/attachments/archive`;
    const failedDownloads: string[] = [];
    const recordDownload = (download: { suggestedFilename(): string }) =>
      failedDownloads.push(download.suggestedFilename());
    op.on("download", recordDownload);
    await op.route(archiveUrl, (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "材料完整性校验失败，未生成部分压缩包",
        }),
      }),
    );
    await op.getByRole("button", { name: "下载全部材料", exact: true }).click();
    await expect(op.locator(".materials").getByRole("alert")).toContainText(
      "材料完整性校验失败",
    );
    expect(failedDownloads).toEqual([]);
    op.off("download", recordDownload);
    await op.unroute(archiveUrl);
    await verifyMaterialDownload(op, rating.id, null);
    await expect(op.locator(".materials").getByRole("alert")).toHaveCount(0);
    await op.screenshot({
      path: "test-results/rating-material-archive.png",
      fullPage: true,
    });
    await op.getByRole("button", { name: "提交星级申报" }).click();
    await expect(op.getByText("提交版本 V2")).toBeVisible();
    const submittedDetail = await (
      await op.request.get(`/api/v1/ratings/applications/${rating.id}`)
    ).json();
    const submitted = submittedDetail.last_submission;
    expect(submitted.current_account_username).toBe("operator_a");
    const receiptTime = new Date(submitted.submitted_at).toLocaleString(
      "zh-CN",
      { timeZone: "Asia/Shanghai", hour12: false },
    );
    await expect(ratingInfo).toContainText(
      `operator_a · #${submitted.actor_id}`,
    );
    await expect(ratingInfo).toContainText(receiptTime);
    await expect(ratingInfo).toContainText("待评审");
    await expect(ratingInfo).toContainText("V2");
    await expect(ratingInfo).not.toContainText("无提交账号记录");
    await op.screenshot({
      path: "test-results/rating-submission-info.png",
      fullPage: true,
    });

    await verifyMaterialDownload(op, rating.id, 2);
    await expect(op.getByTestId(`publication-${rating.id}`)).toContainText(
      "评星中",
    );
    await expect(
      op.getByRole("row", { includeHidden: true }).filter({
        has: op.getByRole("cell", {
          name: `RATE-${rating.id}`,
          exact: true,
          includeHidden: true,
        }),
      }),
    ).toContainText("待评审");
    await navigateWorkspace(assoc, "星级评价");
    const ratingRow = assoc.getByRole("row", { includeHidden: true }).filter({
      has: assoc.getByRole("cell", {
        name: `RATE-${rating.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await ratingRow.getByRole("button", { name: "评星详情" }).click();
    await expect(
      assoc.getByRole("link", { name: "site-plan.zip" }),
    ).toBeVisible();
    await verifyMaterialDownload(assoc, rating.id, 2);
    const associationInfo = assoc.getByTestId("rating-submission-info");
    await expect(associationInfo).toContainText(fixture.name);
    await expect(associationInfo).toContainText(
      `operator_a · #${submitted.actor_id}`,
    );
    await expect(associationInfo).toContainText(receiptTime);
    const regulatorDetail = await reg.request.get(
      `/api/v1/ratings/applications/${rating.id}`,
    );
    expect(regulatorDetail.status()).toBe(200);
    expect((await regulatorDetail.json()).last_submission).toEqual(submitted);

    await assoc.getByLabel("评审意见").fill("补充自评与现场依据");
    await assoc.getByRole("button", { name: "驳回星级申报" }).click();
    await expect(ratingRow).toContainText("已驳回");
    await returnToBusinessList(op, "星级");
    await op.getByRole("button", { name: "刷新评价", exact: true }).click();
    const operatorRatingRow = op
      .getByRole("row", { includeHidden: true })
      .filter({
        has: op.getByRole("cell", {
          name: `RATE-${rating.id}`,
          exact: true,
          includeHidden: true,
        }),
      });
    await operatorRatingRow.getByRole("button", { name: "评星详情" }).click();
    await op.getByLabel("修改自评-service").fill("95");
    await op.getByLabel("修改评星依据").fill("补交现场证据与修正自评");
    await expect(
      op.getByRole("button", { name: "提交星级申报" }),
    ).toBeDisabled();
    await op.getByRole("button", { name: "保存申报修改" }).click();
    await op.getByRole("button", { name: "提交星级申报" }).click();
    await expect(operatorRatingRow).toContainText("待评审");
    await returnToBusinessList(assoc, "星级");
    await assoc.getByRole("button", { name: "刷新评价", exact: true }).click();
    await ratingRow.getByRole("button", { name: "评星详情" }).click();
    await assoc.getByLabel("评审意见").fill("演示复核完成");
    await assoc.getByRole("button", { name: "保存人工复核" }).click();
    await expect(ratingRow).toContainText("89.00");
    await expect(ratingRow).toContainText("已复核待公示");
    await assoc.getByLabel("评审意见").fill("演示公示发布");
    await assoc.getByLabel("公示有效期至").fill("2027-11-15");
    await assoc.getByRole("button", { name: "发布星级结果" }).click();
    await expect(ratingRow).toContainText("已公示");
    await assoc.screenshot({
      path: "test-results/association-rating.png",
      fullPage: true,
    });
    const originalCard = assoc.getByTestId(`publication-${rating.id}`);
    await expect(originalCard).toContainText("有效期至：2027-11-15");
    await expect(originalCard).toContainText("演示评定 3 星");
    const originalReport = await (
      await assoc.request.get(
        `/api/v1/ratings/applications/${rating.id}/report`,
      )
    ).text();
    expect(originalReport).toContain("演示评价报告");
    const reportDownload = assoc.waitForEvent("download");
    await assoc.getByRole("link", { name: "下载评价报告" }).click();
    await (await reportDownload).saveAs("test-results/rating-report.html");
    const reportPreview = await contexts[2].newPage();
    await reportPreview.goto(
      "file://" + resolve("test-results/rating-report.html"),
    );
    await expect(
      reportPreview.getByRole("heading", { name: "充电场站星级评价报告" }),
    ).toBeVisible();
    await reportPreview.screenshot({
      path: "test-results/rating-report.png",
      fullPage: true,
    });
    await reportPreview.close();
    await returnToBusinessList(op, "星级");
    await op.getByRole("button", { name: "刷新评价", exact: true }).click();
    await operatorRatingRow.getByRole("button", { name: "评星详情" }).click();
    await op
      .getByLabel("复评申诉理由")
      .fill("现场评分存在遗漏，请复核补充证据");
    const appealCreated = op.waitForResponse(
      (r) =>
        r.url().endsWith("/ratings/applications") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "创建异议申诉草稿" }).click();
    const appealResponse = await appealCreated;
    expect(appealResponse.status()).toBe(201);
    const appeal = await appealResponse.json();
    expect(appeal.parent_id).toBe(rating.id);
    expect(appeal.kind).toBe("appeal");
    await op.getByRole("button", { name: "提交星级申报" }).click();
    await returnToBusinessList(op, "星级");
    await navigateWorkspace(op, /^(评价结果与公示|公示查询查看)$/);
    await expect(op.getByTestId(`publication-${rating.id}`)).toBeVisible();
    await returnToBusinessList(assoc, "星级");
    await assoc.getByRole("button", { name: "刷新评价", exact: true }).click();
    const appealRow = assoc.getByRole("row", { includeHidden: true }).filter({
      has: assoc.getByRole("cell", {
        name: `RATE-${appeal.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await appealRow.getByRole("button", { name: "评星详情" }).click();
    await assoc.getByLabel("复核-service").fill("100");
    await assoc.getByLabel("复核-safety").fill("100");
    await assoc.getByLabel("评审意见").fill("申诉补充证据已核实");
    await assoc.getByRole("button", { name: "保存人工复核" }).click();
    await expect(appealRow).toContainText("已复核待公示");
    await assoc.getByLabel("公示有效期至").fill("2027-12-31");
    await assoc.getByLabel("评审意见").fill("申诉复核结果公示，替代原结果");
    await assoc.getByRole("button", { name: "发布星级结果" }).click();
    await expect(ratingRow).toContainText("已替代");
    const appealCard = assoc.getByTestId(`publication-${appeal.id}`);
    await expect(appealCard).toContainText("演示评定 5 星");
    await expect(originalCard).toHaveCount(0);
    expect(
      await (
        await assoc.request.get(
          `/api/v1/ratings/applications/${rating.id}/report`,
        )
      ).text(),
    ).toBe(originalReport);
    await assoc.getByLabel("评审意见").fill("演示摘牌，不再显示有效星级标识");
    await assoc.getByRole("button", { name: "摘牌并留存记录" }).click();
    await expect(appealCard).toContainText("已摘牌");
    await expect(appealCard).toContainText("当前有效星级 0");
    await expect(appealCard).not.toContainText("演示评定 5 星");
    await returnToBusinessList(assoc, "星级");
    await navigateWorkspace(assoc, /^(评价结果与公示|公示查询查看)$/);
    await assoc.getByLabel("公示结果状态").selectOption("delisted");
    await assoc.getByLabel("公示场站企业").fill(fixture.name);
    await assoc.getByRole("button", { name: "查询公示", exact: true }).click();
    await expect(appealCard).toBeVisible();
    await assoc.screenshot({
      path: "test-results/rating-appeal-delisted.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
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

test("inspection assignment, enterprise reply and regulator review", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const regContext = await browser.newContext(),
    opContext = await browser.newContext();
  try {
    const reg = await regContext.newPage(),
      op = await opContext.newPage();
    await login(reg, "regulator");
    await login(op, "operator_a");
    await navigateWorkspace(reg, "巡检任务管理");
    await pickStation(reg, "巡检场站", fixture);
    await reg.getByLabel("巡检标题").fill("浏览器隐患整改");
    await reg.getByLabel("巡检要求").fill("检查设备安全防护并提交整改说明");
    await reg.getByLabel("巡检期限").fill("2027-01-01T18:00");
    const created = reg.waitForResponse(
      (r) =>
        r.url().endsWith("/inspections") && r.request().method() === "POST",
    );
    await reg.getByRole("button", { name: "下发任务", exact: true }).click();
    const response = await created;
    expect(response.request().postDataJSON().station_id).toBe(
      fixture.station_id,
    );
    expect(response.status(), await response.text()).toBe(201);
    const task = await response.json();
    await expect(reg.getByLabel("巡检标题", { exact: true })).toHaveValue("");
    await expect(reg.getByLabel("巡检要求", { exact: true })).toHaveText("");
    // Keep this workflow's task visible when another spec adds a page of history.
    await returnToBusinessList(reg, "巡检");
    await reg.getByLabel("巡检检索编号", { exact: true }).fill(String(task.id));
    await reg
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await navigateWorkspace(op, "整改任务");
    await op.getByLabel("巡检检索编号", { exact: true }).fill(String(task.id));
    await op.getByRole("button", { name: "查询巡检记录", exact: true }).click();
    const row = op.getByRole("row", { includeHidden: true }).filter({
      has: op.getByRole("cell", {
        name: `TASK-${task.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await row.getByRole("button", { name: "处理任务" }).click();
    await op.getByLabel("巡检处理说明").fill("上传期间保留整改说明");
    let releaseUpload!: () => void, arrived!: () => void;
    const holdUpload = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    const uploadArrived = new Promise<void>((resolve) => {
      arrived = resolve;
    });
    const uploadUrl = `**/api/v1/inspections/${task.id}/attachments`;
    await op.route(uploadUrl, async (route) => {
      if (route.request().method() === "POST") {
        arrived();
        await holdUpload;
      }
      await route.continue();
    });
    await expect(op.getByLabel("上传整改材料")).toBeEnabled();
    await op.getByLabel("上传整改材料").setInputFiles({
      name: "整改照片.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfFoAAAAASUVORK5CYII=",
        "base64",
      ),
    });
    try {
      await uploadArrived;
      await expect(
        op.getByRole("button", { name: "提交整改回复", exact: true }),
      ).toBeDisabled();
      await expect(
        op.getByRole("button", { name: "关闭任务详情", exact: true }),
      ).toBeDisabled();
      await expect(
        op.getByLabel("巡检检索编号", { exact: true }),
      ).not.toBeVisible();
      await expect(
        op.getByRole("heading", {
          name: `TASK-${task.id} · 浏览器隐患整改`,
          exact: true,
        }),
      ).toBeVisible();
    } finally {
      releaseUpload();
    }
    await expect(op.getByRole("link", { name: "整改照片.png" })).toBeVisible();
    await op.unroute(uploadUrl);
    const materialRefresh = op.waitForResponse(
      (response) =>
        response.url().endsWith(`/inspections/${task.id}/attachments`) &&
        response.request().method() === "GET",
    );
    await op.getByRole("button", { name: "刷新数据", exact: true }).click();
    expect((await materialRefresh).status()).toBe(200);
    await expect(op.getByLabel("巡检处理说明")).toHaveText(
      "上传期间保留整改说明",
    );
    await op.getByLabel("巡检处理说明").fill("已完成设备防护整改");
    await op.getByLabel("巡检执行联系人", { exact: true }).fill("现场执行人");
    await op
      .getByLabel("巡检执行联系方式", { exact: true })
      .fill("13800000001");
    await op
      .getByLabel("巡检填报执行时间", { exact: true })
      .fill("2026-11-01T10:30");
    await op.getByLabel("巡检处理说明").press("ControlOrMeta+a");
    await op.getByRole("button", { name: "加粗", exact: true }).click();
    await expect(op.getByLabel("巡检处理说明").locator("strong")).toHaveText(
      "已完成设备防护整改",
    );
    await op.getByLabel("巡检处理说明").locator("p").last().click();
    await op.getByLabel("巡检处理说明").press("End");
    await expect
      .poll(() =>
        op
          .getByLabel("巡检处理说明")
          .evaluate((el) => el.ownerDocument.getSelection()?.isCollapsed),
      )
      .toBe(true);
    await op
      .getByRole("button", { name: "插入回复图片：整改照片.png", exact: true })
      .click();
    await expect(op.getByLabel("巡检处理说明").locator("strong")).toHaveText(
      "已完成设备防护整改",
    );
    await expect(
      op.getByRole("button", { name: "提交整改回复", exact: true }),
    ).toBeEnabled();
    await op.getByRole("button", { name: "提交整改回复" }).click();
    await expect(row).toContainText("待复核");
    await reg.getByRole("button", { name: "刷新任务", exact: true }).click();
    const regRow = reg.getByRole("row", { includeHidden: true }).filter({
      has: reg.getByRole("cell", {
        name: `TASK-${task.id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await regRow.getByRole("button", { name: "处理任务" }).click();
    await expect(
      reg.getByText("已完成设备防护整改", { exact: true }),
    ).toBeVisible();
    await expect(reg.getByText("现场执行人", { exact: true })).toBeVisible();
    await expect(reg.getByText("13800000001", { exact: true })).toBeVisible();
    await expect(
      reg
        .getByRole("article")
        .filter({ hasText: "现场执行人" })
        .locator("strong"),
    ).toHaveText("已完成设备防护整改");
    await expect(
      reg.getByRole("article").filter({ hasText: "现场执行人" }).locator("img"),
    ).toBeVisible();
    const evidence = reg.getByRole("link", { name: "整改照片.png" });
    await expect(evidence).toBeVisible();
    expect(
      (await reg.request.get((await evidence.getAttribute("href"))!)).status(),
    ).toBe(200);
    await reg.getByLabel("巡检处理说明").fill("演示核查确认");
    await reg.getByRole("button", { name: "复核关闭任务" }).click();
    await expect(regRow).toContainText("已关闭");
    await reg.screenshot({
      path: "test-results/inspection-review.png",
      fullPage: true,
    });
    await reg.setViewportSize({ width: 390, height: 844 });
    expect(
      await reg.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await reg
      .getByRole("article")
      .filter({ hasText: "现场执行人" })
      .screenshot({ path: "test-results/inspection-execution-mobile.png" });
  } finally {
    await regContext.close();
    await opContext.close();
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

test("alert samples merge, recover and remain visible to the owning enterprise", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const regContext = await browser.newContext(),
    opContext = await browser.newContext();
  try {
    const reg = await regContext.newPage(),
      op = await opContext.newPage();
    await login(reg, "regulator");
    await login(op, "operator_a");
    await navigateWorkspace(reg, "告警数据");
    await reg
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "规则配置", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "演示样本验证", exact: true })
      .click();
    await pickStation(reg, "告警演示场站", fixture);
    await reg
      .getByLabel("告警监测对象")
      .fill("browser-target-" + fixture.station_id);
    async function sample(value: string) {
      await reg
        .getByRole("navigation", { name: "告警工作区" })
        .getByRole("button", { name: "规则配置", exact: true })
        .click();
      await reg.getByLabel("告警样本值").fill(value);
      const done = reg.waitForResponse(
        (r) =>
          r.url().endsWith("/alerts/samples") &&
          r.request().method() === "POST",
      );
      await reg.getByRole("button", { name: "提交演示样本" }).click();
      const response = await done;
      expect(response.status(), await response.text()).toBe(200);
      await reg
        .getByRole("navigation", { name: "告警工作区" })
        .getByRole("button", { name: "事件跟踪", exact: true })
        .click();
      return response.json();
    }
    const opened = await sample("40");
    expect(opened.outcome).toBe("opened");
    const row = reg.getByRole("row", { includeHidden: true }).filter({
      has: reg.getByRole("cell", {
        name: `ALERT-${opened.event_id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await expect(row).toContainText("持续异常");
    const merged = await sample("50");
    expect(merged.event_id).toBe(opened.event_id);
    expect(merged.outcome).toBe("merged");
    await expect(
      row.getByRole("cell", { name: "2", exact: true }),
    ).toBeVisible();
    const recovered = await sample("0");
    expect(recovered.outcome).toBe("recovered");
    await expect(row).toContainText("已恢复");
    await row.getByRole("button", { name: "告警详情" }).click();
    await expect(reg.getByText(/演示告警：.*触发/)).toBeVisible();
    await expect(reg.getByText(/演示告警：.*恢复/)).toBeVisible();
    await reg.getByLabel("告警确认依据").fill("演示异常与恢复已核查");
    await reg.getByRole("button", { name: "确认已查看告警" }).click();
    await expect(row).toContainText("已人工确认");
    await navigateWorkspace(op, "企业告警");
    const owned = op.getByRole("row", { includeHidden: true }).filter({
      has: op.getByRole("cell", {
        name: `ALERT-${opened.event_id}`,
        exact: true,
        includeHidden: true,
      }),
    });
    await expect(owned).toContainText("已恢复");
    await op
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "规则配置", exact: true })
      .click();
    await expect(op.getByRole("button", { name: "演示样本验证" })).toHaveCount(
      0,
    );
    await reg.screenshot({
      path: "test-results/alert-recovery.png",
      fullPage: true,
    });
  } finally {
    await regContext.close();
    await opContext.close();
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

test("published notice reaches the enterprise and scoped energy statistics export", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [reg, op, assoc] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(reg, "regulator");
    await login(op, "operator_a");
    await login(assoc, "association");
    await navigateWorkspace(reg, "自定义通知发布");
    await reg.getByRole("button", { name: "新建内容草稿" }).click();
    await reg.getByLabel("内容类型", { exact: true }).selectOption("notice");
    await reg.getByLabel("查找收件企业", { exact: true }).fill("演示运营商甲");
    await reg
      .getByRole("button", { name: "筛选收件企业", exact: true })
      .click();
    await reg
      .getByRole("checkbox", { name: "收件企业：演示运营商甲", exact: true })
      .check();
    await reg.getByLabel("内容标题", { exact: true }).fill(fixture.name);
    await reg
      .getByLabel("内容正文", { exact: true })
      .fill("请回复本期场站安全核查情况");
    await reg.getByRole("button", { name: "保存内容草稿" }).click();
    const row = reg.getByRole("row", { includeHidden: true }).filter({
      has: reg.getByRole("cell", { name: fixture.name, exact: true }),
    });
    await expect(reg.locator(".record-detail")).toContainText("草稿");
    await navigateWorkspace(op, /^(公告与通知|新增发布资讯)$/);
    await expect(
      op.getByRole("row", { includeHidden: true }).filter({
        has: op.getByRole("cell", { name: fixture.name, exact: true }),
      }),
    ).toHaveCount(0);
    await reg.getByRole("button", { name: "发布内容", exact: true }).click();
    await expect(reg.locator(".record-detail")).toContainText("已发布");
    await op.getByRole("button", { name: "查询内容", exact: true }).click();
    const visible = op.getByRole("row", { includeHidden: true }).filter({
      has: op.getByRole("cell", { name: fixture.name, exact: true }),
    });
    await visible.getByRole("button", { name: "查看内容" }).click();
    await op.getByLabel("通知回复", { exact: true }).fill("企业核查已完成");
    await op.getByLabel("通知回复", { exact: true }).press("ControlOrMeta+a");
    await op.getByRole("button", { name: "加粗", exact: true }).click();
    await op.getByRole("button", { name: "链接", exact: true }).click();
    await op
      .getByLabel("正文链接地址", { exact: true })
      .fill("https://example.com/reply-proof");
    // Move to the contact field in the same event turn as applying a link.
    // A deferred editor focus must not steal the next field's input.
    await op
      .getByRole("button", { name: "应用链接", exact: true })
      .evaluate((button) => {
        (button as HTMLButtonElement).click();
        document
          .querySelector<HTMLInputElement>('input[aria-label="回复联系人"]')!
          .focus();
      });
    await op.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(op.getByLabel("回复联系人", { exact: true })).toBeFocused();
    await expect(op.getByLabel("通知回复", { exact: true })).toHaveText(
      "企业核查已完成",
    );
    await op.getByLabel("回复联系人", { exact: true }).fill("张先生");
    await op.getByLabel("回复联系方式", { exact: true }).fill("0591-12345678");
    await op.getByLabel("通知回复图片", { exact: true }).setInputFiles({
      name: "inspection.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOLsAAAAASUVORK5CYII=",
        "base64",
      ),
    });
    await op.getByLabel("通知回复", { exact: true }).press("ControlOrMeta+a");
    await op.getByLabel("通知回复", { exact: true }).press("ArrowRight");
    await op
      .getByRole("button", {
        name: "插入回复图片：inspection.png",
        exact: true,
      })
      .click();
    await expect(
      op.getByLabel("通知回复", { exact: true }).locator("img"),
    ).toHaveCount(1);
    const replyCommitted = op.waitForResponse(
      (r) =>
        r.url().endsWith("/replies/with-images") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "提交通知回复" }).click();
    expect((await replyCommitted).status()).toBe(201);
    // Wait for the immutable rendered reply, independently of the editor.
    await expect(
      op
        .locator(".timeline .rich-document, .timeline .article-body")
        .filter({ hasText: /^企业核查已完成$/ }),
    ).toBeVisible();
    await expect(op.locator(".notice-reply .rich-document strong")).toHaveText(
      "企业核查已完成",
    );
    await expect(op.locator(".notice-reply .rich-document a")).toHaveAttribute(
      "href",
      "https://example.com/reply-proof",
    );
    await reg.getByRole("button", { name: "关闭内容详情" }).click();
    await row.getByRole("button", { name: "查看内容" }).click();
    await expect(
      reg.getByText("企业核查已完成", { exact: true }),
    ).toBeVisible();
    await expect(
      reg.getByText("回复联系人：张先生 · 联系方式：0591-12345678", {
        exact: true,
      }),
    ).toBeVisible();
    const replyImage = reg.getByRole("img", {
      name: "回复图片：inspection.png",
      exact: true,
    });
    await expect(replyImage).toBeVisible();
    await expect
      .poll(() =>
        replyImage.evaluate(
          (img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
        ),
      )
      .toBe(true);
    await reg.screenshot({
      path: "test-results/notice-image-reply.png",
      fullPage: true,
    });
    await navigateWorkspace(op, /^(统计分析|充电量统计)$/);
    await op.getByLabel("统计开始日期").fill("2026-11-01");
    await op.getByLabel("统计结束日期").fill("2026-11-02");
    await op.getByRole("button", { name: "查询统计", exact: true }).click();
    const energy = op.getByRole("row", { includeHidden: true }).filter({
      has: op.getByRole("cell", { name: fixture.name, exact: true }),
    });
    await expect(energy).toContainText("42.005000");
    await op.getByText("查看周期观测与比较明细", { exact: true }).click();
    await expect(op.getByTestId("period-2026-11-02")).toContainText("暂无数据");
    const exportLink = op.getByRole("link", { name: "导出场站电量CSV" });
    await expect(exportLink).toBeVisible();
    expect(
      (await op.request.get((await exportLink.getAttribute("href"))!)).status(),
    ).toBe(200);
    await op.getByLabel("统计周期", { exact: true }).selectOption("month");
    await op
      .getByLabel("统计排名维度", { exact: true })
      .selectOption("district");
    const monthlyResponse = op.waitForResponse(
      (r) =>
        r.url().includes("/statistics?") &&
        r.url().includes("granularity=month"),
    );
    await op.getByRole("button", { name: "查询统计", exact: true }).click();
    const monthly = await (await monthlyResponse).json();
    expect(Number(monthly.total_kwh)).toBeGreaterThanOrEqual(42.005);
    await expect(
      op.getByRole("heading", { name: "按月电量走势", exact: true }),
    ).toBeVisible();
    await expect(
      op.getByRole("heading", { name: "区域电量排行", exact: true }),
    ).toBeVisible();
    await expect(op.getByTestId("period-2026-11")).toContainText(
      monthly.timeline[0].kwh,
    );
    const analysisLink = op.getByRole("link", {
      name: "导出周期分析CSV",
      exact: true,
    });
    const analysis = await op.request.get(
      (await analysisLink.getAttribute("href"))!,
    );
    expect(analysis.status()).toBe(200);
    expect(await analysis.text()).toContain("观测同比%");
    await op.getByLabel("统计周期", { exact: true }).selectOption("year");
    await op
      .getByLabel("统计排名维度", { exact: true })
      .selectOption("operator");
    await op.getByRole("button", { name: "查询统计", exact: true }).click();
    await expect(
      op.getByRole("heading", { name: "按年电量走势", exact: true }),
    ).toBeVisible();
    await expect(
      op.getByRole("heading", { name: "运营商电量排行", exact: true }),
    ).toBeVisible();
    await navigateWorkspace(assoc, /^(统计分析|充电量统计)$/);
    await expect(
      assoc.getByRole("link", { name: "导出场站电量CSV" }),
    ).toHaveCount(0);
    await op.screenshot({
      path: "test-results/statistics-observations.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
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
