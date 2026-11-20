import { navigateWorkspace, test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("content batch keeps cross-page versions, preserves deleted history and exports applied queries", async ({
  page,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("regulator");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
    const me = await (await page.request.get("/api/v1/auth/me")).json();
    const options = await (
      await page.request.get(
        "/api/v1/content/operator-options?q=" +
          encodeURIComponent("演示运营商甲"),
      )
    ).json();
    const ids: number[] = [];
    for (let index = 0; index < 26; index++) {
      const created = await page.request.post("/api/v1/content", {
        headers: { "x-csrf-token": me.csrf_token },
        data: {
          kind: "notice",
          title: fixture.name,
          body: "通知批量流程核查正文",
          audience: "operator",
          recipient_ids: [options.items[0].id],
        },
      });
      expect(created.status()).toBe(201);
      ids.push((await created.json()).id);
    }
    await navigateWorkspace(page, "自定义通知发布");
    await page.getByLabel("搜索内容标题", { exact: true }).fill(fixture.name);
    await page.getByRole("button", { name: "查询内容", exact: true }).click();
    await expect(
      page.getByText("第1页 · 共26条", { exact: true }),
    ).toBeVisible();
    await page.getByLabel(`选择草稿 ${ids[25]}`, { exact: true }).check();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByLabel(`选择草稿 ${ids[0]}`, { exact: true }).check();
    await page
      .getByRole("button", { name: "批量发布草稿", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "批量操作确认" }),
    ).toContainText("共2份");
    await page
      .getByRole("button", { name: "确认发布所选草稿", exact: true })
      .click();
    await expect(
      page.getByText("已选0/100份草稿（跨页保留版本）", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("row").filter({
        has: page.getByRole("cell", { name: `POST-${ids[0]}`, exact: true }),
      }),
    ).toContainText("已发布");
    await page.getByLabel(`选择草稿 ${ids[1]}`, { exact: true }).check();
    await page
      .getByRole("button", { name: "批量删除草稿", exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认删除所选草稿", exact: true })
      .click();
    await expect(
      page.getByText("第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("显示已删除内容", { exact: true }).check();
    await expect(
      page.getByText("第1页 · 共26条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    const deleted = page.getByRole("row").filter({
      has: page.getByRole("cell", { name: `POST-${ids[1]}`, exact: true }),
    });
    await expect(deleted).toContainText("已删除（保留历史）");
    await deleted
      .getByRole("button", { name: "查看内容", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "编辑草稿正文", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "发布内容台账", exact: true }),
    ).toBeFocused();
    await page
      .getByLabel("搜索内容标题", { exact: true })
      .fill("尚未应用的关键词");
    const exportLink = page.getByRole("link", {
      name: "导出当前查询内容元数据",
      exact: true,
    });
    const href = (await exportLink.getAttribute("href"))!;
    expect(new URL(href, "http://localhost").searchParams.get("q")).toBe(
      fixture.name,
    );
    const exported = await page.request.get(href);
    expect(exported.status()).toBe(200);
    expect((await exported.text()).trim().split(/\r?\n/)).toHaveLength(27);
    expect(await exported.text()).not.toContain("通知批量流程核查正文");
    await page
      .getByRole("button", { name: "展开通知记录", exact: true })
      .click();
    await page.getByLabel("通知记录关键词", { exact: true }).fill(fixture.name);
    await page
      .getByRole("button", { name: "查询通知记录", exact: true })
      .click();
    const records = page.locator("section.panel").filter({
      has: page.getByRole("heading", { name: "逐企业通知记录", exact: true }),
    });
    await expect(
      records.getByText("第1页 · 共2条", { exact: true }),
    ).toBeVisible();
    const recordsExport = await page.request.get(
      (await records
        .getByRole("link", { name: "导出当前查询通知记录", exact: true })
        .getAttribute("href"))!,
    );
    expect(recordsExport.status()).toBe(200);
    expect((await recordsExport.text()).trim().split(/\r?\n/)).toHaveLength(3);
    await page.screenshot({
      path: "test-results/content-operations.png",
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
