import { test, expect, navigateWorkspace, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
const createStation = (operating = false) =>
  JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        ...(operating ? ["--operating"] : []),
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
const clean = (id: number) =>
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
test("association landing uses the prototype station query and mobile header stays compact", async ({
  page,
}) => {
  await login(page, "association");
  await expect(
    page.getByRole("heading", { name: "多维度数据筛选", level: 1 }),
  ).toBeVisible();
  await expect(page.getByLabel("搜索场站", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/module=/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "多维度数据筛选", level: 1 }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../.backups/secondary-association-directory.png",
  });
  await page.setViewportSize({ width: 390, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const more = page.getByRole("button", { name: "更多功能", exact: true });
  const box = await more.boundingBox();
  expect(box!.height).toBeLessThan(40);
  await page.screenshot({
    path: "../../.backups/secondary-association-390.png",
  });
});
test("association process shows real frozen actions and results manage station publications", async ({
  browser,
}) => {
  const fixture = createStation(true);
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [op, assoc] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(op, "operator_a");
    await login(assoc, "association");
    const opIdentity = await (await op.request.get("/api/v1/auth/me")).json(),
      identity = await (await assoc.request.get("/api/v1/auth/me")).json();
    const standards = await (
      await op.request.get("/api/v1/ratings/standards")
    ).json();
    const standard = standards.items.find(
      (r: { code: string; version: number }) =>
        r.code === "DEMO-RATING" && r.version === 1,
    );
    const created = await op.request.post("/api/v1/ratings/applications", {
      headers: { "x-csrf-token": opIdentity.csrf_token },
      data: {
        station_id: fixture.station_id,
        standard_id: standard.id,
        scores: { service: "90", safety: "90" },
        evidence_note: "角色页面校正测试",
      },
    });
    expect(created.status()).toBe(201);
    let record = await created.json();
    for (const [page, csrf, action] of [
      [op, opIdentity.csrf_token, "submit"],
      [assoc, identity.csrf_token, "review"],
      [assoc, identity.csrf_token, "publish"],
    ] as const) {
      const response = await page.request.post(
        `/api/v1/ratings/applications/${record.id}/actions`,
        {
          headers: { "x-csrf-token": csrf },
          data: {
            action,
            version: record.version,
            comment: "校正验证",
            ...(action === "review"
              ? { scores: { service: "95", safety: "95" } }
              : {}),
            ...(action === "publish" ? { valid_until: "2027-12-31" } : {}),
          },
        },
      );
      expect(response.status()).toBe(200);
      record = await response.json();
    }
    await navigateWorkspace(assoc, "评审过程管理");
    await assoc
      .getByLabel("评审过程场站企业", { exact: true })
      .fill(fixture.name);
    await assoc
      .getByLabel("评审过程动作", { exact: true })
      .selectOption("review");
    await expect(
      assoc.getByRole("button", { name: "查询评审过程", exact: true }),
    ).toBeEnabled();
    await assoc
      .getByRole("button", { name: "查询评审过程", exact: true })
      .click();
    const region = assoc.getByRole("region", {
      name: "评审过程记录",
      exact: true,
    });
    await expect(
      region.getByRole("row").filter({ hasText: `RATE-${record.id}` }),
    ).toHaveCount(1);
    await expect(region).toContainText("association");
    const response = assoc.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/ratings/process/export",
    );
    const file = assoc.waitForEvent("download");
    await assoc
      .getByRole("button", { name: "导出评审过程", exact: true })
      .click();
    expect((await response).status()).toBe(200);
    expect((await file).suggestedFilename()).toBe("ronghub-rating-process.csv");
    await assoc.screenshot({
      path: "../../.backups/secondary-association-process.png",
    });
    await navigateWorkspace(assoc, "评价结果管理");
    await assoc.locator(".rating-result-directory > summary").click();
    await assoc
      .getByLabel("公示场站企业", { exact: true })
      .last()
      .fill(fixture.name);
    const results = assoc
      .getByRole("heading", { name: "评价结果管理", level: 2 })
      .locator("xpath=ancestor::section[1]");
    await results
      .getByRole("button", { name: "查询公示", exact: true })
      .click();
    const row = assoc.getByTestId(`rating-result-${record.id}`);
    await expect(row).toBeVisible();
    await expect(
      results.getByRole("columnheader", { name: "当前有效星级", exact: true }),
    ).toBeVisible();
    await expect(
      assoc.getByLabel("星级筛选状态", { exact: true }),
    ).toBeHidden();
    await row
      .getByRole("button", { name: "管理评定结果", exact: true })
      .click();
    await expect(
      assoc.getByRole("button", { name: "摘牌并留存记录", exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
    clean(fixture.station_id);
  }
});
test("operator progress is read only until explicitly entering application handling", async ({
  page,
}) => {
  const fixture = createStation();
  try {
    await login(page, "operator_a");
    const identity = await (await page.request.get("/api/v1/auth/me")).json();
    const response = await page.request.post("/api/v1/applications", {
      headers: { "x-csrf-token": identity.csrf_token },
      data: {
        station_id: fixture.station_id,
        kind: "access",
        payload: { note: "进度查询只读验证" },
      },
    });
    expect(response.status()).toBe(201);
    const record = await response.json();
    await navigateWorkspace(page, "场站接入进展查看");
    await expect(
      page.getByRole("button", { name: "重置申请筛选", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "重置申请筛选", exact: true })
      .click();
    await expect(page).toHaveURL(/view=progress/);
    await page.getByLabel("检索申请场站或企业").fill(fixture.name);
    await expect(
      page.getByRole("button", { name: "查询申请", exact: true }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "查询申请", exact: true }).click();
    await page
      .getByRole("row")
      .filter({ hasText: `APP-${record.id}` })
      .getByRole("button", { name: "查看进展", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "保存申请信息", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel("上传申请材料", { exact: true })).toHaveCount(
      0,
    );
    await page
      .getByRole("button", { name: "前往接入申请办理", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "保存申请信息", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "../../.backups/secondary-operator-progress.png",
    });
  } finally {
    clean(fixture.station_id);
  }
});
test("operator primary pages replace repeated view tabs with contextual actions", async ({
  page,
}) => {
  await login(page, "operator_a");
  await navigateWorkspace(page, "申报材料填报");
  await expect(page.locator(".workspace-related-actions")).toHaveCount(0);
  await expect(
    page.getByRole("navigation", { name: "星级评价业务视图", exact: true }),
  ).toHaveCount(0);
  await page
    .getByText("自评与补充说明（可在草稿中继续完善）", { exact: true })
    .click();
  const note = page.getByLabel("评星依据说明", { exact: true });
  await note.fill("尚未保存的申报说明");
  const back = page.getByRole("button", { name: "返回评定进度", exact: true });
  await back.click();
  await expect(
    page.getByRole("button", { name: "查询星级记录", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新增星级申报", exact: true }).click();
  await expect(note).toHaveValue("尚未保存的申报说明");
  page.once("dialog", (dialog) => dialog.dismiss());
  await navigateWorkspace(page, "企业资料");
  await expect(note).toHaveValue("尚未保存的申报说明");
  page.once("dialog", (dialog) => dialog.accept());
  await navigateWorkspace(page, "评定进度查询");
  await navigateWorkspace(page, "评价标准查看");
  await expect(page.locator(".active-group > button")).toContainText(
    "星级站申报",
  );
  await expect(
    page
      .getByRole("navigation", { name: "辅助功能导航", includeHidden: true })
      .getByRole("button", { name: "评价标准查看", includeHidden: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "星级评价标准", exact: true }),
  ).toBeVisible();
  await navigateWorkspace(page, "补贴记录查询与导出");
  await expect(
    page.getByRole("heading", { name: "补贴记录查询与导出", level: 2 }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../.backups/secondary-operator-records.png",
  });
});

test("operator station rows open the correct intake paths and preserve station selection", async ({
  page,
}) => {
  const pending = createStation();
  const operating = createStation(true);
  const showStation = async (name: string) => {
    await navigateWorkspace(page, "信息推送");
    await page.getByLabel("搜索场站", { exact: true }).fill(name);
    await page.getByRole("button", { name: "查询", exact: true }).click();
    return page.getByRole("row").filter({ hasText: name });
  };

  try {
    await login(page, "operator_a");
    const pendingRow = await showStation(pending.name);
    await expect(
      pendingRow.getByRole("button", { name: "星级申报", exact: true }),
    ).toHaveCount(0);
    await pendingRow
      .getByRole("button", { name: "接入申请", exact: true })
      .click();
    await expect(page.getByLabel("申请场站", { exact: true })).toHaveValue(
      String(pending.station_id),
    );
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/applications") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "新建草稿", exact: true }).click();
    const record = await (await created).json();
    await expect(
      page.getByRole("heading", { name: `APP-${record.id} · 场站接入` }),
    ).toBeVisible();
    await (
      await showStation(pending.name)
    )
      .getByRole("button", { name: "接入申请", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: `APP-${record.id} · 场站接入` }),
    ).toBeVisible();
    await page.getByRole("button", { name: "关闭详情", exact: true }).click();
    await expect(
      page.getByRole("row").filter({ hasText: `APP-${record.id}` }),
    ).toContainText("无提交记录");
    await page.getByRole("button", { name: "补充材料", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "申请材料办理" }),
    ).toBeFocused();

    await (
      await showStation(operating.name)
    )
      .getByRole("button", { name: "接入变更", exact: true })
      .click();
    await expect(page.getByLabel("申请场站", { exact: true })).toHaveValue(
      String(operating.station_id),
    );
    await expect(page.getByLabel("申请类型", { exact: true })).toHaveValue(
      "change",
    );

    await (
      await showStation(operating.name)
    )
      .getByRole("button", { name: "星级申报", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "申报材料填报", level: 1 }),
    ).toBeVisible();
    await expect(page.getByLabel("评星场站", { exact: true })).toHaveValue(
      String(operating.station_id),
    );
    expect(new URL(page.url()).searchParams.has("station_id")).toBe(false);
  } finally {
    clean(pending.station_id);
    clean(operating.station_id);
  }
});
