import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page
    .getByRole("button", { name: /^(公告与通知|新增发布资讯)$/, exact: true })
    .click();
}
test("multi-company notice creates independent records and isolates enterprise replies", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext()));
  try {
    const [reg, own, other] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(reg, "regulator");
    await reg
      .getByRole("button", { name: "自定义通知发布", exact: true })
      .click();
    await login(own, "operator_a");
    await login(other, "operator_b");
    await reg
      .getByRole("button", { name: "新建内容草稿", exact: true })
      .click();
    await reg.getByLabel("内容类型", { exact: true }).selectOption("notice");
    await reg.getByLabel("内容标题", { exact: true }).fill(fixture.name);
    await reg
      .getByLabel("内容正文", { exact: true })
      .fill("请两家企业分别回复安全核查情况。");
    await reg.getByLabel("查找收件企业", { exact: true }).fill("演示运营商甲");
    await reg
      .getByRole("button", { name: "筛选收件企业", exact: true })
      .click();
    await reg
      .getByRole("checkbox", { name: "收件企业：演示运营商甲", exact: true })
      .check();
    await reg.getByLabel("查找收件企业", { exact: true }).fill("演示运营商乙");
    await reg
      .getByRole("button", { name: "筛选收件企业", exact: true })
      .click();
    await reg
      .getByRole("checkbox", { name: "收件企业：演示运营商乙", exact: true })
      .check();
    await expect(
      reg.getByText(
        "已选2/100家。发布后生成逐企业记录；不会发送短信或邮件。搜索和翻页保留选择。",
      ),
    ).toBeVisible();
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    await expect(reg.locator(".record-detail")).toContainText(fixture.name);
    await reg
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    const row = reg.getByRole("row").filter({
      has: reg.getByRole("cell", { name: fixture.name, exact: true }),
    });
    await row.getByRole("button", { name: "查看内容", exact: true }).click();
    await reg.getByRole("button", { name: "发布内容", exact: true }).click();
    await expect(
      reg.getByRole("heading", { name: "逐企业站内记录", exact: true }),
    ).toBeVisible();
    await reg
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "展开通知记录", exact: true })
      .click();
    await reg.getByLabel("通知记录关键词", { exact: true }).fill(fixture.name);
    await reg
      .getByRole("button", { name: "查询通知记录", exact: true })
      .click();
    await expect(reg.getByText("第1页 · 共2条", { exact: true })).toBeVisible();
    for (const page of [own, other]) {
      await page.getByLabel("搜索内容标题", { exact: true }).fill(fixture.name);
      await page.getByRole("button", { name: "查询内容", exact: true }).click();
      await page
        .getByRole("row")
        .filter({
          has: page.getByRole("cell", { name: fixture.name, exact: true }),
        })
        .getByRole("button", { name: "查看内容", exact: true })
        .click();
    }
    await own.getByLabel("通知回复", { exact: true }).fill("甲企业独立反馈");
    await own.getByLabel("回复联系人", { exact: true }).fill("甲方联系人");
    await own.getByLabel("回复联系方式", { exact: true }).fill("13800000000");
    await own
      .getByRole("button", { name: "提交通知回复", exact: true })
      .click();
    await expect(
      own
        .locator(".timeline .rich-document, .timeline .article-body")
        .filter({ hasText: "甲企业独立反馈" }),
    ).toBeVisible();
    await other
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    await other.getByRole("button", { name: "查询内容", exact: true }).click();
    await other
      .getByRole("row")
      .filter({
        has: other.getByRole("cell", { name: fixture.name, exact: true }),
      })
      .getByRole("button", { name: "查看内容", exact: true })
      .click();
    await expect(
      other.getByText("甲企业独立反馈", { exact: true }),
    ).toHaveCount(0);
    await expect(other.getByText("甲方联系人", { exact: false })).toHaveCount(
      0,
    );
    await reg.getByLabel("通知记录回复", { exact: true }).selectOption("true");
    await reg
      .getByRole("button", { name: "查询通知记录", exact: true })
      .click();
    const records = reg.locator("section.panel").filter({
      has: reg.getByRole("heading", { name: "逐企业通知记录", exact: true }),
    });
    await expect(
      records.getByText("第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await expect(
      records.getByRole("cell", { name: "演示运营商甲", exact: true }),
    ).toBeVisible();
    await expect(
      records.getByRole("cell", { name: "1条", exact: true }),
    ).toBeVisible();
    await expect(
      records.getByRole("cell", { name: "演示运营商乙", exact: true }),
    ).toHaveCount(0);

    await row.getByRole("button", { name: "查看内容", exact: true }).click();
    await expect(
      reg
        .locator(".timeline .rich-document, .timeline .article-body")
        .filter({ hasText: "甲企业独立反馈" }),
    ).toBeVisible();
    await reg.screenshot({
      path: "test-results/notice-deliveries.png",
      fullPage: true,
    });
    await reg
      .getByRole("button", { name: "关闭内容详情", exact: true })
      .click();
    await expect(reg.getByLabel("通知记录关键词", { exact: true })).toHaveValue(
      fixture.name,
    );
    await expect(reg.getByLabel("通知记录回复", { exact: true })).toHaveValue(
      "true",
    );
    await expect(
      records.getByText("第1页 · 共1条", { exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
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
