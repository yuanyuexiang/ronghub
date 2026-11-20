import { returnToBusinessList } from "./business-workspace";
import { test, expect, openWorkspaceNavigation, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture() {
  return JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
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
async function queue(page: Page, q: string) {
  await openWorkspaceNavigation(page);
  await page.getByRole("button", { name: "隐患督办", exact: true }).click();
  await page.getByLabel("督办检索名称", { exact: true }).fill(q);
  await page.getByRole("button", { name: "查询督办", exact: true }).click();
}
async function create(page: Page, station: { name: string }) {
  await page.getByText("登记新隐患", { exact: true }).click();
  await page.getByLabel("督办目标检索", { exact: true }).fill(station.name);
  await page.getByRole("button", { name: "查找督办目标", exact: true }).click();
  await page
    .getByRole("region", { name: "督办目标选择", exact: true })
    .getByRole("button")
    .filter({ hasText: station.name })
    .click();
  await page
    .getByLabel("督办隐患名称", { exact: true })
    .fill("督办验收-" + station.name);
  await page
    .getByLabel("督办目标运营商", { exact: true })
    .selectOption({ index: 1 });
  await page.getByLabel("督办隐患分级", { exact: true }).selectOption("major");
  await page
    .getByLabel("督办整改期限", { exact: true })
    .fill("2030-01-01T10:00");
  await page
    .getByLabel("督办隐患说明", { exact: true })
    .fill("核查独立隐患并提交实际整改结果及证明材料");
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/hazards") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "登记并发布督办", exact: true })
    .click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const item = await response.json();
  await expect(
    page.getByRole("region", { name: "隐患督办详情", exact: true }),
  ).toContainText("未开始");
  return item;
}
async function open(page: Page, id: number) {
  const row = page
    .getByRole("row")
    .filter({ has: page.getByText(`HAZ-${id}`, { exact: true }) });
  await row.getByRole("button", { name: "查看督办", exact: true }).click();
  return page.getByRole("region", { name: "隐患督办详情", exact: true });
}

test("hazard completion keeps tracking until explicit regulator closure and isolates evidence", async ({
  browser,
}) => {
  const value = fixture();
  const contexts = await Promise.all(
    [0, 1, 2, 3].map(() => browser.newContext()),
  );
  try {
    const [reg, owner, other, association] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    for (const [i, page] of [reg, owner, other, association].entries())
      await login(
        page,
        ["regulator", "operator_a", "operator_b", "association"][i],
      );
    await queue(reg, value.name);
    const item = await create(reg, value);
    await queue(owner, value.name);
    const detail = await open(owner, item.id);
    await detail
      .getByRole("button", { name: "确认开始整改", exact: true })
      .click();
    await expect(detail).toContainText("整改中");
    await expect(
      detail.getByRole("button", { name: "提交完成反馈", exact: true }),
    ).toBeDisabled();
    await detail.getByLabel("上传督办证明材料", { exact: true }).setInputFiles({
      name: "督办证据.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nBrowser hazard proof"),
    });
    await expect(
      detail.getByRole("link", { name: "督办证据.pdf", exact: true }),
    ).toBeVisible();
    await detail
      .getByLabel("督办执行联系人", { exact: true })
      .fill("验收执行人");
    await detail
      .getByLabel("督办执行联系方式", { exact: true })
      .fill("13800000001");
    await detail
      .getByLabel("督办完成时间", { exact: true })
      .fill("2026-11-01T10:00");
    await detail
      .getByLabel("督办整改结果", { exact: true })
      .fill("现场隐患已整改，企业填报完成但监管继续跟踪");
    await detail
      .getByRole("button", { name: "提交完成反馈", exact: true })
      .click();
    await expect(owner.getByRole("status")).toContainText("监管跟踪仍继续");
    await expect(detail).toContainText("企业已填报完成");
    await expect(detail).toContainText("跟踪中");
    await expect(detail).toContainText("验收执行人");
    await expect(
      detail.getByRole("button", {
        name: "删除督办材料 督办证据.pdf",
        exact: true,
      }),
    ).toHaveCount(0);
    const material = (
      await (await owner.request.get(`/api/v1/hazards/${item.id}`)).json()
    ).materials[0];
    const materialPath = `/api/v1/hazards/${item.id}/attachments/${material.id}/download`;
    expect((await other.request.get(materialPath)).status()).toBe(404);
    expect((await association.request.get(materialPath)).status()).toBe(403);
    await expect(
      association.getByRole("button", { name: "隐患督办", exact: true }),
    ).toHaveCount(0);
    await queue(other, value.name);
    await expect(
      other.getByText("督办第1页 · 共0条", { exact: true }),
    ).toBeVisible();
    const regDetail = reg.getByRole("region", {
      name: "隐患督办详情",
      exact: true,
    });
    await regDetail
      .getByRole("button", { name: "重新载入督办详情", exact: true })
      .click();
    await expect(regDetail).toContainText("企业已填报完成");
    await regDetail
      .getByLabel("督办关闭依据", { exact: true })
      .fill("核查证据后决定结束监管跟踪");
    await regDetail
      .getByRole("button", { name: "关闭督办跟踪", exact: true })
      .click();
    await regDetail
      .getByRole("button", { name: "取消关闭督办", exact: true })
      .click();
    await expect(regDetail).toContainText("跟踪中");
    await regDetail
      .getByRole("button", { name: "关闭督办跟踪", exact: true })
      .click();
    await regDetail
      .getByRole("button", { name: "确认关闭督办", exact: true })
      .click();
    await expect(regDetail).toContainText("已关闭");
    await expect(regDetail).toContainText("企业已填报完成");
    await detail
      .getByRole("button", { name: "重新载入督办详情", exact: true })
      .click();
    await expect(
      detail.getByLabel("上传督办证明材料", { exact: true }),
    ).toHaveCount(0);
    await expect(
      detail.getByRole("button", { name: "提交完成反馈", exact: true }),
    ).toHaveCount(0);
    await returnToBusinessList(reg, "督办");
    await reg
      .getByLabel("督办检索名称", { exact: true })
      .fill("unapplied-hazard-draft");
    const downloading = reg.waitForEvent("download");
    await reg
      .getByRole("button", { name: "导出督办 CSV", exact: true })
      .click();
    const downloaded = await downloading;
    expect(downloaded.suggestedFilename()).toBe("ronghub-hazards.csv");
    const content = readFileSync((await downloaded.path())!, "utf8");
    expect(content).toContain(item.title);
    expect(content).toContain("企业已填报完成,已关闭");
    await reg.screenshot({
      path: "test-results/hazard-closed-desktop.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    clean(value.station_id);
  }
});

test("regulator can close an unstarted hazard without inventing execution facts and preserves unsaved reason at narrow width", async ({
  page,
}) => {
  const value = fixture();
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, "regulator");
    await queue(page, value.name);
    const item = await create(page, value);
    const detail = page.getByRole("region", {
      name: "隐患督办详情",
      exact: true,
    });
    await detail
      .getByLabel("督办关闭依据", { exact: true })
      .fill("根据现场核查终止跟踪，不认定企业完成");
    await detail
      .getByRole("button", { name: "重新载入督办详情", exact: true })
      .click();
    await expect(
      page
        .getByRole("region", { name: "隐患督办工作区", exact: true })
        .getByRole("alert"),
    ).toContainText("未保存修改");
    await page
      .getByRole("button", { name: "保留督办反馈", exact: true })
      .click();
    await expect(
      detail.getByLabel("督办关闭依据", { exact: true }),
    ).toHaveValue("根据现场核查终止跟踪，不认定企业完成");
    await detail
      .getByRole("button", { name: "关闭督办跟踪", exact: true })
      .click();
    await detail
      .getByRole("button", { name: "确认关闭督办", exact: true })
      .click();
    await expect(detail).toContainText("未开始");
    await expect(detail).toContainText("已关闭");
    const actual = await (
      await page.request.get(`/api/v1/hazards/${item.id}`)
    ).json();
    expect(actual.progress).toBe("not_started");
    expect(
      actual.history.every(
        (action: { performed_at: string | null }) =>
          action.performed_at === null,
      ),
    ).toBe(true);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "test-results/hazard-unstarted-closed-390.png",
      fullPage: true,
    });
  } finally {
    clean(value.station_id);
  }
});
