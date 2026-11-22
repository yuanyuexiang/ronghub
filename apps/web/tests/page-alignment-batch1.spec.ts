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

import { pickStation } from "./station";

test("regulator process and both station result workspaces show actual events, profiles and daily metrics", async ({
  browser,
}) => {
  const fixture = createStation(true),
    contexts = await Promise.all([
      browser.newContext(),
      browser.newContext(),
      browser.newContext(),
    ]);
  try {
    const [op, assoc, reg] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    for (const [page, user] of [
      [op, "operator_a"],
      [assoc, "association"],
      [reg, "regulator"],
    ] as const)
      await login(page, user);
    const opMe = await (await op.request.get("/api/v1/auth/me")).json(),
      assMe = await (await assoc.request.get("/api/v1/auth/me")).json();
    const standards = await (
        await op.request.get("/api/v1/ratings/standards")
      ).json(),
      standard = standards.items.find(
        (s: { code: string; version: number }) =>
          s.code === "DEMO-RATING" && s.version === 1,
      );
    await navigateWorkspace(reg, "评审结果查看");
    await pickStation(reg, "评价结果场站", fixture);
    await reg
      .getByRole("button", { name: "查询场站结果", exact: true })
      .click();
    await expect(
      reg.getByRole("article", { name: "场站概况与评价结果", exact: true }),
    ).toContainText("演示运营商甲");
    await expect(
      reg.getByRole("article", { name: "场站概况与评价结果", exact: true }),
    ).toContainText("暂无已提交评定结果");
    await reg
      .getByLabel("单站电量来源", { exact: true })
      .fill("audit-no-such-source");
    await reg
      .getByRole("button", { name: "查询单站电量", exact: true })
      .click();
    await expect(
      reg.getByRole("region", { name: "每日运营数据空表", exact: true }),
    ).toBeVisible();
    await expect(
      reg
        .getByRole("region", { name: "每日运营数据空表", exact: true })
        .getByRole("columnheader"),
    ).toHaveCount(6);
    const created = await op.request.post("/api/v1/ratings/applications", {
      headers: { "x-csrf-token": opMe.csrf_token },
      data: {
        station_id: fixture.station_id,
        standard_id: standard.id,
        scores: { service: "95", safety: "95" },
        evidence_note: "页面内容校正验证",
      },
    });
    expect(created.status()).toBe(201);
    let record = await created.json();
    for (const [page, csrf, action] of [
      [op, opMe.csrf_token, "submit"],
      [assoc, assMe.csrf_token, "review"],
      [assoc, assMe.csrf_token, "publish"],
    ] as const) {
      const r = await page.request.post(
        `/api/v1/ratings/applications/${record.id}/actions`,
        {
          headers: { "x-csrf-token": csrf },
          data: {
            action,
            version: record.version,
            comment: "复核依据",
            ...(action === "review"
              ? { scores: { service: "95", safety: "95" } }
              : {}),
            ...(action === "publish" ? { valid_until: "2027-12-31" } : {}),
          },
        },
      );
      expect(r.status()).toBe(200);
      record = await r.json();
    }
    await navigateWorkspace(reg, "评审过程查看");
    await reg
      .getByLabel("评审过程场站企业", { exact: true })
      .fill(fixture.name);
    await expect(
      reg.getByRole("button", { name: "查询评审过程", exact: true }),
    ).toBeEnabled();
    await reg
      .getByRole("button", { name: "查询评审过程", exact: true })
      .click();
    await expect(
      reg.getByRole("region", { name: "评审过程记录", exact: true }),
    ).toContainText(`RATE-${record.id}`);
    await expect(
      reg.getByRole("columnheader", {
        name: "变更前星级 / 有效期",
        exact: true,
      }),
    ).toBeVisible();
    await reg.screenshot({
      path: "../../.backups/page-batch1-regulator-process.png",
    });
    for (const [page, label] of [
      [reg, "评审结果查看"],
      [assoc, "评价结果管理"],
    ] as const) {
      await navigateWorkspace(page, label);
      await pickStation(page, "评价结果场站", fixture);
      await page
        .getByRole("button", { name: "查询场站结果", exact: true })
        .click();
      const card = page.getByRole("article", {
        name: "场站概况与评价结果",
        exact: true,
      });
      await expect(card).toContainText(fixture.name);
      await expect(card).toContainText("5 星");
      await expect(card).toContainText("2027-12-31");
      await expect(
        page.getByRole("heading", { name: "每日运营数据", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("columnheader", {
          name: "充电时长（小时）",
          exact: true,
        }),
      ).toBeVisible();
      if (page === assoc) {
        await expect(card).toContainText("当前岗位不可查看");
        await card.getByRole("button", { name: "升降级", exact: true }).click();
        await expect(page.getByText(/现有升降级采用复评流程/)).toBeVisible();
        await card
          .getByRole("button", { name: "摘牌办理", exact: true })
          .click();
        await expect(
          page.getByRole("button", { name: "摘牌并留存记录", exact: true }),
        ).toBeVisible();
        await page
          .getByRole("button", { name: "关闭评价详情", exact: true })
          .click();
        await expect(card).toContainText(fixture.name);
      }
      await page.screenshot({
        path: `../../.backups/page-batch1-${page === reg ? "regulator" : "association"}-result.png`,
      });
    }
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
    clean(fixture.station_id);
  }
});

test("operator selects categorized materials before draft creation and uploads into the same draft", async ({
  page,
}) => {
  const fixture = createStation(true);
  try {
    await login(page, "operator_a");
    await navigateWorkspace(page, "申报材料填报");
    await expect(
      page.getByRole("region", { name: "申报材料填报清单", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".rating-self-assessment")).not.toHaveAttribute(
      "open",
      /.*/,
    );
    await expect(
      page.getByLabel("填报-site_plan", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "../../.backups/page-batch1-material-intake.png",
    });
    await page.setViewportSize({ width: 390, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../../.backups/page-batch1-material-intake-390.png",
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await pickStation(page, "评星场站", fixture);
    await page.getByLabel("填报-site_plan", { exact: true }).setInputFiles({
      name: "intake-site-plan.zip",
      mimeType: "application/zip",
      buffer: Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x01]),
    });
    const created = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/ratings/applications" &&
        r.request().method() === "POST",
    );
    const uploaded = page.waitForResponse(
      (r) =>
        r.url().includes("/attachments") && r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存评星草稿", exact: true })
      .click();
    const response = await created;
    expect(response.status()).toBe(201);
    const record = await response.json();
    expect((await uploaded).status()).toBe(201);
    await expect(
      page.getByRole("heading", { name: new RegExp(`^RATE-${record.id} ·`) }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "intake-site-plan.zip", exact: true }),
    ).toBeVisible();
    const materials = await (
      await page.request.get(
        `/api/v1/ratings/applications/${record.id}/attachments`,
      )
    ).json();
    expect(materials.items).toHaveLength(1);
    expect(materials.items[0].category).toBe("site_plan");
    await page.setViewportSize({ width: 390, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../../.backups/page-batch1-operator-materials-390.png",
    });
  } finally {
    clean(fixture.station_id);
  }
});

test("operator finalized ledger excludes pending applications, exports applied filters and opens readonly evidence", async ({
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
        "--subsidy-pending",
        "2",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [op, reg] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(op, "operator_a");
    await login(reg, "regulator");
    const me = await (await reg.request.get("/api/v1/auth/me")).json();
    const cid = fixture.claim_ids[0];
    for (const action of ["approve", "record_disbursement"]) {
      const current = await (
        await reg.request.get(`/api/v1/subsidy/claims/${cid}`)
      ).json();
      const response = await reg.request.post(
        `/api/v1/subsidy/claims/${cid}/actions`,
        {
          headers: { "x-csrf-token": me.csrf_token },
          data: {
            action,
            version: current.version,
            comment: "本地页面验证登记，不代表真实支付",
          },
        },
      );
      expect(response.status()).toBe(200);
    }
    await navigateWorkspace(op, "补贴记录查询与导出");
    await op.getByLabel("补贴台账名称", { exact: true }).fill(fixture.name);
    await expect(
      op.getByRole("button", { name: "查询补贴台账", exact: true }),
    ).toBeEnabled();
    await op.getByRole("button", { name: "查询补贴台账", exact: true }).click();
    const ledger = op.getByRole("region", {
      name: "补贴生成与发放记录",
      exact: true,
    });
    await expect(
      ledger.getByRole("row").filter({ hasText: `SUB-${cid}` }),
    ).toHaveCount(1);
    await expect(ledger).not.toContainText(`SUB-${fixture.claim_ids[1]}`);
    await expect(ledger).toContainText("已登记发放");
    await op
      .getByLabel("补贴台账名称", { exact: true })
      .fill("unapplied-draft");
    const download = op.waitForEvent("download");
    const response = op.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/subsidy/ledger/export",
    );
    await op.getByRole("button", { name: "导出补贴记录", exact: true }).click();
    expect((await response).status()).toBe(200);
    expect(new URL((await response).url()).searchParams.get("q")).toBe(
      fixture.name,
    );
    expect((await download).suggestedFilename()).toBe(
      "ronghub-subsidy-ledger.csv",
    );
    await ledger.getByRole("button", { name: "补贴明细", exact: true }).click();
    const detail = op.getByRole("region", { name: "补贴明细", exact: true });
    await expect(detail.getByRole("table")).toContainText(fixture.name);
    const exportedDetail = op.waitForEvent("download");
    await detail
      .getByRole("button", { name: "导出补贴明细", exact: true })
      .click();
    expect((await exportedDetail).suggestedFilename()).toBe(
      `SUB-${cid}-补贴明细.csv`,
    );
    await detail.getByRole("button", { name: "订单明细", exact: true }).click();
    const orders = op.getByRole("region", { name: "订单明细", exact: true });
    await expect(orders).toContainText("当前无补贴与订单的逐笔关联");
    await expect(orders).toContainText("该场站在此核算周期内暂无源报订单");
    await orders
      .getByRole("button", { name: "返回补贴明细", exact: true })
      .click();
    await detail
      .getByRole("button", { name: "查看申请核算依据", exact: true })
      .click();
    await expect(
      op.getByRole("button", { name: "前往补贴申请办理", exact: true }),
    ).toBeVisible();
    await expect(op.getByLabel("上传补贴ZIP材料", { exact: true })).toHaveCount(
      0,
    );
    await op.getByRole("button", { name: "关闭补贴详情", exact: true }).click();
    await detail
      .getByRole("button", { name: "返回补贴台账", exact: true })
      .click();
    await expect(op.getByLabel("补贴台账名称", { exact: true })).toHaveValue(
      "unapplied-draft",
    );
    await expect(ledger).toContainText(`SUB-${cid}`);
    await op.screenshot({
      path: "../../.backups/page-batch1-operator-ledger.png",
    });
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
    clean(fixture.station_id);
  }
});

test("material draft receipt survives a detail read failure without creating a duplicate application", async ({
  page,
}) => {
  const fixture = createStation(true);
  try {
    await login(page, "operator_a");
    await navigateWorkspace(page, "申报材料填报");
    await pickStation(page, "评星场站", fixture);
    let creates = 0,
      fail = true;
    page.on("request", (r) => {
      if (
        new URL(r.url()).pathname === "/api/v1/ratings/applications" &&
        r.method() === "POST"
      )
        creates++;
    });
    await page.route("**/api/v1/ratings/applications/*", (route) => {
      if (
        fail &&
        route.request().method() === "GET" &&
        /\/applications\/\d+$/.test(new URL(route.request().url()).pathname)
      ) {
        fail = false;
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "模拟详情读取失败" }),
        });
      }
      return route.continue();
    });
    await page
      .getByRole("button", { name: "保存评星草稿", exact: true })
      .click();
    await expect(page.getByText(/已保存材料草稿 RATE-/)).toBeVisible();
    await expect(page.getByLabel("评星场站", { exact: true })).toBeDisabled();
    await page
      .getByRole("button", { name: "继续已保存草稿", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: /^RATE-\d+ ·/ }),
    ).toBeVisible();
    expect(creates).toBe(1);
  } finally {
    clean(fixture.station_id);
  }
});
