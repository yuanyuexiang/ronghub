import { returnToBusinessList } from "./business-workspace";
import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, account: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(account);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}

test("global refresh keeps applied queries, pages, dirty forms and details while fencing stale responses", async ({
  page,
}) => {
  test.setTimeout(90000);
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
        "--business-history",
        "25",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await login(page, "operator_a");
    const csrf = (await (await page.request.get("/api/v1/auth/me")).json())
      .csrf_token;
    for (const [label, menu, base, ids, detailButton, prefix] of [
      [
        "补贴",
        "补贴申报",
        "/api/v1/subsidy/claims",
        fixture.claim_ids,
        "补贴详情",
        "SUB",
      ],
      [
        "星级",
        "星级申报",
        "/api/v1/ratings/applications",
        fixture.rating_ids,
        "评星详情",
        "RATE",
      ],
    ] as const) {
      await page.getByRole("button", { name: menu, exact: true }).click();
      if (label === "补贴") {
        await page
          .getByRole("button", { name: "政策与单站申报", exact: true })
          .click();
        await page
          .getByLabel("补贴开始日期", { exact: true })
          .fill("2026-10-15");
      } else {
        await page
          .getByRole("button", { name: "新增星级申报", exact: true })
          .click();
        await page
          .getByLabel("评星依据说明", { exact: true })
          .fill("未保存的评星依据");
      }
      await returnToBusinessList(page, label);
      await page.getByLabel(`${label}检索名称`).fill(fixture.name);
      await page.getByLabel(`${label}筛选状态`).selectOption("withdrawn");
      await page
        .getByRole("button", { name: `查询${label}记录`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第1页 · 共25条`, { exact: true }),
      ).toBeVisible();
      await page
        .getByRole("button", { name: `${label}下一页`, exact: true })
        .click();
      await expect(
        page.getByText(`${label}第2页 · 共25条`, { exact: true }),
      ).toBeVisible();
      await page.getByLabel(`${label}检索名称`).fill("not-applied-search");
      await page
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", {
            name: `${prefix}-${ids[0]}`,
            exact: true,
          }),
        })
        .getByRole("button", { name: detailButton, exact: true })
        .click();
      await expect(
        page.getByRole("heading", {
          name: new RegExp(`^${prefix}-${ids[0]} ·`),
        }),
      ).toBeVisible();

      let release = () => {},
        held = false;
      let capturedResolve = () => {},
        completedResolve = () => {};
      const captured = new Promise<void>((resolve) => {
        capturedResolve = resolve;
      });
      const completed = new Promise<void>((resolve) => {
        completedResolve = resolve;
      });
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      const pattern = `**${base}?*`;
      await page.route(pattern, async (route) => {
        const url = new URL(route.request().url());
        if (
          !held &&
          url.searchParams.get("q") === fixture.name &&
          url.searchParams.get("page") === "2"
        ) {
          held = true;
          const response = await route.fetch();
          capturedResolve();
          await gate;
          try {
            await route.fulfill({ response });
          } catch {
            /* Refresh aborted the obsolete browser request. */
          }
          completedResolve();
        } else await route.continue();
      });
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      await captured;
      const old = await (await page.request.get(`${base}/${ids[0]}`)).json();
      const body =
        label === "补贴"
          ? {
              station_id: fixture.station_id,
              policy_id: old.policy_id,
              start_date: old.start_date,
              end_date: old.end_date,
            }
          : {
              station_id: fixture.station_id,
              standard_id: old.standard_id,
              kind: "initial",
              scores: {},
              evidence_note: "另一设备创建的演示记录",
            };
      const created = await page.request.post(base, {
        headers: { "x-csrf-token": csrf },
        data: body,
      });
      expect(created.status()).toBe(201);
      const record = await created.json();
      expect(
        (
          await page.request.post(`${base}/${record.id}/actions`, {
            headers: { "x-csrf-token": csrf },
            data: { action: "withdraw", version: record.version },
          })
        ).status(),
      ).toBe(200);
      const refreshed = page.waitForRequest(
        (request) =>
          new URL(request.url()).pathname === base &&
          request.method() === "GET" &&
          new URL(request.url()).searchParams.get("page") === "2",
      );
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      expect(new URL((await refreshed).url()).searchParams.get("q")).toBe(
        fixture.name,
      );
      await expect(
        page.getByText(`${label}第2页 · 共26条`, { exact: true }),
      ).toHaveText(`${label}第2页 · 共26条`);
      release();
      await completed;
      await page.evaluate(() => new Promise(requestAnimationFrame));
      await expect(
        page.getByText(`${label}第2页 · 共26条`, { exact: true }),
      ).toHaveText(`${label}第2页 · 共26条`);
      await page.unroute(pattern);
      await expect(page.getByLabel(`${label}检索名称`)).toHaveValue(
        "not-applied-search",
      );
      await expect(
        page.getByRole("heading", {
          name: new RegExp(`^${prefix}-${ids[0]} ·`),
        }),
      ).toBeVisible();
      if (label === "补贴")
        await expect(
          page.getByLabel("补贴开始日期", { exact: true }),
        ).toHaveValue("2026-10-15");
      else
        await expect(
          page.getByLabel("评星依据说明", { exact: true }),
        ).toHaveValue("未保存的评星依据");

      await returnToBusinessList(page, label);
      await page.route(pattern, (route) =>
        route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "列表暂不可用" }),
        }),
      );
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      await expect(
        page.getByText("列表暂不可用", { exact: true }),
      ).toBeVisible();
      await expect(page.locator(".page-refresh-controls")).not.toContainText(
        "刷新成功",
      );
      await page.unroute(pattern);
      await page.getByRole("button", { name: "刷新数据", exact: true }).click();
      await expect(
        page.getByText(`${label}第2页 · 共26条`, { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: `查询${label}记录`, exact: true }),
      ).toBeEnabled();
      await expect(
        page.getByRole("button", { name: detailButton, exact: true }),
      ).toHaveCount(6);
      await expect(
        page.locator(".page-refresh-controls").getByRole("status"),
      ).toContainText("已发起列表与概览刷新");
      if (label === "星级")
        await page.screenshot({
          path: "test-results/page-refresh.png",
          fullPage: true,
        });
    }
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

test("regulator refresh reaches mounted view readers without applying search drafts or writing", async ({
  page,
}) => {
  test.setTimeout(90000);
  await login(page, "regulator");
  const mutations: string[] = [];
  page.on("request", (request) => {
    if (["POST", "PATCH", "DELETE"].includes(request.method()))
      mutations.push(new URL(request.url()).pathname);
  });
  for (const [menu, path] of [
    ["人员账号申请", "/api/v1/staff-applications"],
    ["运营商审核", "/api/v1/registrations"],
    ["账号管理", "/api/v1/accounts"],
    ["场站运营商管理", "/api/v1/companies"],
    ["新增发布资讯", "/api/v1/content"],
    ["充电量统计", "/api/v1/statistics"],
    ["接入管理", "/api/v1/applications"],
    ["告警数据", "/api/v1/alerts"],
    ["巡检任务管理", "/api/v1/inspections"],
    ["场站数据同步", "/api/v1/sync/tasks"],
    ["充电订单", "/api/v1/orders"],
    ["订单汇总", "/api/v1/orders/summary"],
    ["告警分析", "/api/v1/statistics/faults"],
    ["账号安全", "/api/v1/auth/phone"],
    ["企业申报查看", "/api/v1/ratings/applications"],
    ["补贴申请记录", "/api/v1/subsidy/claims"],
    ["可视化一张图", "/api/v1/map"],
  ]) {
    const first = page.waitForResponse(
      (response) => new URL(response.url()).pathname === path,
    );
    await page.getByRole("button", { name: menu, exact: true }).click();
    expect((await first).status()).toBe(200);
    if (["运营商审核", "账号管理", "场站运营商管理"].includes(menu))
      await page.locator("form.search input").first().fill("unapplied-draft");
    const refreshed = page.waitForResponse(
      (response) => new URL(response.url()).pathname === path,
    );
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    const response = await refreshed;
    expect(response.status()).toBe(200);
    if (["运营商审核", "账号管理", "场站运营商管理"].includes(menu)) {
      expect(new URL(response.url()).searchParams.get("q")).toBe("");
      await expect(page.locator("form.search input").first()).toHaveValue(
        "unapplied-draft",
      );
    }
  }
  expect(mutations).toEqual([]);
});

test("automatic page clamp after external draft deletion preserves the open editor and detail", async ({
  page,
}) => {
  test.setTimeout(90000);
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await login(page, "regulator");
    const csrf = (await (await page.request.get("/api/v1/auth/me")).json())
      .csrf_token;
    const ids: number[] = [];
    for (let i = 0; i < 26; i++) {
      const response = await page.request.post("/api/v1/content", {
        headers: { "x-csrf-token": csrf },
        data: {
          kind: "announcement",
          title: fixture.name,
          body: "原始草稿",
          audience: "all",
        },
      });
      expect(response.status()).toBe(201);
      ids.push((await response.json()).id);
    }
    await page
      .getByRole("button", { name: /^(公告与通知|新增发布资讯)$/, exact: true })
      .click();
    await page.getByLabel("搜索内容标题", { exact: true }).fill(fixture.name);
    await page.getByRole("button", { name: "查询内容", exact: true }).click();
    await expect(
      page.getByText("第1页 · 共26条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page
      .getByRole("row")
      .filter({ has: page.getByLabel(`选择草稿 ${ids[0]}`, { exact: true }) })
      .getByRole("button", { name: "查看内容", exact: true })
      .click();
    await page
      .getByRole("button", { name: "编辑草稿正文", exact: true })
      .click();
    await page
      .getByLabel("内容标题", { exact: true })
      .fill(fixture.name + "-未保存标题");
    const editor = page.getByLabel("内容正文", { exact: true });
    await editor.fill("未保存的正文不能因自动回页而丢失");
    const deleted = await page.request.post("/api/v1/content/batch-actions", {
      headers: { "x-csrf-token": csrf },
      data: {
        action: "delete",
        items: ids.slice(6).map((id) => ({ id, version: 1 })),
      },
    });
    expect(deleted.status()).toBe(200);
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(page.getByText("第1页 · 共6条", { exact: true })).toBeHidden();
    await expect(page.locator(".record-pane")).toContainText("第1页 · 共6条");
    await expect(page.getByLabel("内容标题", { exact: true })).toHaveValue(
      fixture.name + "-未保存标题",
    );
    await expect(editor).toContainText("未保存的正文不能因自动回页而丢失");
    await expect(
      page.getByRole("button", { name: "编辑草稿正文", exact: true }),
    ).toBeHidden();
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
