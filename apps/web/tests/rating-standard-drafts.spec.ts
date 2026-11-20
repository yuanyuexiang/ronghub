import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /^(运营总览|接入汇总)$/, exact: true }),
  ).toBeVisible();
}
function cleanup(code: string) {
  execFileSync(
    "uv",
    ["run", "python", "-m", "app.rating_standard_fixture", "--code", code],
    { cwd: "../server" },
  );
}
async function csrf(page: Page) {
  return (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token;
}
test("standard draft survives partial save, refresh and conflict then finalizes and archives with frozen revisions", async ({
  page,
}) => {
  const code = "browser-config-" + randomBytes(8).toString("hex"),
    name = "草稿定稿 " + code;
  try {
    await login(page, "association");
    await navigateWorkspace(page, "星级评价");
    await navigateWorkspace(page, "评价标准管理");
    await page
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    const work = page.getByRole("region", {
      name: "标准草稿工作区",
      exact: true,
    });
    let form = work.locator("form.standard-builder");
    await form.getByLabel("标准代码", { exact: true }).fill(code);
    await form.getByLabel("标准名称", { exact: true }).fill(name);
    const today = await form
      .getByLabel("指标1生效日期", { exact: true })
      .inputValue();
    await form.getByLabel("版本", { exact: true }).fill("");
    await form.getByLabel("指标1生效日期", { exact: true }).fill("");
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standard-drafts") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "暂存标准草稿", exact: true })
      .click();
    const draft = await (await created).json();
    expect(draft.data.version).toBe("");
    expect(draft.data.indicators[0].effective_from).toBe("");
    await expect(
      work.getByText(`草稿 #${draft.id} · 编辑中 · 编辑版本 1`, {
        exact: true,
      }),
    ).toBeVisible();
    await form
      .getByLabel("标准名称", { exact: true })
      .fill("未保存输入 " + code);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(form.getByLabel("标准名称", { exact: true })).toHaveValue(
      "未保存输入 " + code,
    );
    const token = await csrf(page);
    const changed = await page.request.patch(
      `/api/v1/ratings/standard-drafts/${draft.id}`,
      {
        headers: { "x-csrf-token": token },
        data: {
          version: 1,
          data: { ...draft.data, name: "服务器修改 " + code },
        },
      },
    );
    expect(changed.status()).toBe(200);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    const conflict = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/standard-drafts/${draft.id}`) &&
        r.request().method() === "PATCH",
    );
    await form
      .getByRole("button", { name: "暂存标准草稿", exact: true })
      .click();
    expect((await conflict).status()).toBe(409);
    await expect(work.getByRole("alert")).toContainText("草稿版本已变化");
    await expect(form.getByLabel("标准名称", { exact: true })).toHaveValue(
      "未保存输入 " + code,
    );
    await work
      .getByRole("button", { name: `载入标准草稿 #${draft.id}`, exact: true })
      .click();
    const confirm = work.getByRole("region", {
      name: "载入草稿确认",
      exact: true,
    });
    await expect(confirm).toContainText("未保存修改");
    await confirm
      .getByRole("button", { name: "保留当前输入", exact: true })
      .click();
    await expect(form.getByLabel("标准名称", { exact: true })).toHaveValue(
      "未保存输入 " + code,
    );
    await work
      .getByRole("button", { name: `载入标准草稿 #${draft.id}`, exact: true })
      .click();
    await work
      .getByRole("button", { name: "确认载入草稿", exact: true })
      .click();
    form = work.locator("form.standard-builder");
    await expect(form.getByLabel("标准名称", { exact: true })).toHaveValue(
      "服务器修改 " + code,
    );
    await form.getByLabel("版本", { exact: true }).fill("1");
    await form.getByLabel("指标1生效日期", { exact: true }).fill(today);
    await form.getByLabel("标准名称", { exact: true }).fill(name);
    const saved = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/standard-drafts/${draft.id}`) &&
        r.request().method() === "PATCH",
    );
    await form
      .getByRole("button", { name: "暂存标准草稿", exact: true })
      .click();
    expect((await (await saved).json()).version).toBe(3);
    const finalized = page.waitForResponse((r) =>
      r.url().endsWith(`/standard-drafts/${draft.id}/finalize`),
    );
    await form
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const result = await (await finalized).json();
    expect(result.draft.status).toBe("published");
    expect(result.draft.version).toBe(4);
    const standard = result.standard;
    await expect(
      page.getByRole("region", { name: "标准草稿工作区", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: `停用标准 ${code} V1`, exact: true })
      .click();
    const state = page.getByRole("region", {
      name: `标准状态确认 ${standard.id}`,
      exact: true,
    });
    await expect(state).toContainText("管理修订 1");
    const external = await page.request.post(
      `/api/v1/ratings/standards/${standard.id}/availability`,
      {
        headers: { "x-csrf-token": token },
        data: { revision: 1, enabled: false },
      },
    );
    expect(external.status()).toBe(200);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await state
      .getByRole("button", { name: "确认标准状态办理", exact: true })
      .click();
    await expect(
      page.getByRole("alert").filter({ hasText: "标准管理版本已变化" }),
    ).toBeVisible();
    await state
      .getByRole("button", { name: "取消标准状态办理", exact: true })
      .click();
    await page
      .getByRole("button", { name: `启用标准 ${code} V1`, exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认标准状态办理", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: `停用标准 ${code} V1`, exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: `删除标准 ${code} V1`, exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认标准状态办理", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: `删除标准 ${code} V1`, exact: true }),
    ).toHaveCount(0);
    const archived = await (
      await page.request.get(`/api/v1/ratings/standards/${standard.id}`)
    ).json();
    expect(archived.archived).toBe(true);
    expect(archived.revision).toBe(4);
    await page
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    await page.getByText(/已删除归档标准（\d+）/).click();
    await expect(
      page.getByText(`${name} · ${code} V1`, { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/standard-draft-archived.png",
      fullPage: true,
    });
  } finally {
    cleanup(code);
  }
});

test("referenced standards can be disabled but not deleted and deleted drafts stay read-only", async ({
  browser,
}) => {
  const code = "browser-config-" + randomBytes(8).toString("hex"),
    name = "引用保护 " + code;
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
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
    const saved = ass.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standards") &&
        r.request().method() === "POST",
    );
    await ass
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const standard = await (await saved).json();
    await login(op, "operator_a");
    const ot = await csrf(op),
      at = await csrf(ass);
    const application = await op.request.post("/api/v1/ratings/applications", {
      headers: { "x-csrf-token": ot },
      data: {
        station_id: fixture.station_id,
        standard_id: standard.id,
        scores: { service: "90", safety: "80" },
      },
    });
    expect(application.status()).toBe(201);
    const claim = await application.json();
    const path = `/api/v1/ratings/applications/${claim.id}`;
    const before = await (await op.request.get(path)).json();
    await ass
      .getByRole("button", { name: `删除标准 ${code} V1`, exact: true })
      .click();
    await ass
      .getByRole("button", { name: "确认标准状态办理", exact: true })
      .click();
    await expect(
      ass.getByRole("alert").filter({ hasText: "标准已被申请引用" }),
    ).toBeVisible();
    await ass
      .getByRole("button", { name: "取消标准状态办理", exact: true })
      .click();
    await ass
      .getByRole("button", { name: `停用标准 ${code} V1`, exact: true })
      .click();
    await ass
      .getByRole("button", { name: "确认标准状态办理", exact: true })
      .click();
    await expect(
      ass.getByRole("button", { name: `启用标准 ${code} V1`, exact: true }),
    ).toBeEnabled();
    const denied = await op.request.post("/api/v1/ratings/applications", {
      headers: { "x-csrf-token": ot },
      data: {
        station_id: fixture.station_id,
        standard_id: standard.id,
        scores: { service: "90", safety: "80" },
      },
    });
    expect(denied.status()).toBe(409);
    expect((await denied.json()).detail).toContain("停用");
    expect(
      (
        await op.request.post(path + "/actions", {
          headers: { "x-csrf-token": ot },
          data: { action: "submit", version: 1 },
        })
      ).status(),
    ).toBe(200);
    expect((await (await op.request.get(path)).json()).rule_snapshot).toEqual(
      before.rule_snapshot,
    );
    const created = await ass.request.post("/api/v1/ratings/standard-drafts", {
      headers: { "x-csrf-token": at },
      data: { data: { code, name: "待删除草稿", version: "" } },
    });
    expect(created.status()).toBe(201);
    const draft = await created.json();
    expect(
      (
        await op.request.get(`/api/v1/ratings/standard-drafts/${draft.id}`)
      ).status(),
    ).toBe(403);
    await ass
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    const work = ass.getByRole("region", {
      name: "标准草稿工作区",
      exact: true,
    });
    await work
      .getByRole("button", { name: `载入标准草稿 #${draft.id}`, exact: true })
      .click();
    await expect(work.getByLabel("标准名称", { exact: true })).toHaveValue(
      "待删除草稿",
    );
    await work
      .getByRole("button", { name: `删除标准草稿 #${draft.id}`, exact: true })
      .click();
    await work
      .getByRole("button", { name: "确认删除标准草稿", exact: true })
      .click();
    await expect(
      work.getByRole("button", { name: "暂存标准草稿", exact: true }),
    ).toBeDisabled();
    await expect(
      work.getByText(`草稿 #${draft.id} · 已删除留档 · 编辑版本 2`, {
        exact: true,
      }),
    ).toBeVisible();
    await work
      .getByLabel("标准草稿状态", { exact: true })
      .selectOption("deleted");
    await work
      .getByRole("button", { name: "查询标准草稿", exact: true })
      .click();
    await expect(
      work.getByRole("button", {
        name: `载入标准草稿 #${draft.id}`,
        exact: true,
      }),
    ).toBeVisible();
    await ass.screenshot({
      path: "test-results/standard-draft-deleted.png",
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
    cleanup(code);
  }
});
