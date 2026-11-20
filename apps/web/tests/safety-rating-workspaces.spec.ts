import { test, expect, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { pickStation } from "./station";
import { returnToBusinessList } from "./business-workspace";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(...args: string[]) {
  return JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", ...args],
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
async function login(page: Page, account: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(account);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}

test("safety details preserve paged queries and protect unsaved replies and acknowledgment", async ({
  browser,
}) => {
  const value = fixture("--inspection-history", "25", "--alert-history", "25");
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [op, reg] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(op, "operator_a");
    await login(reg, "regulator");
    await op.getByRole("button", { name: "整改任务", exact: true }).click();
    await op.getByLabel("巡检检索名称", { exact: true }).fill(value.name);
    await op.getByRole("button", { name: "查询巡检记录", exact: true }).click();
    await expect(
      op.getByText("巡检第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await op.getByRole("button", { name: "巡检下一页", exact: true }).click();
    await op
      .getByRole("row")
      .filter({
        has: op.getByRole("cell", {
          name: `TASK-${value.task_ids[0]}`,
          exact: true,
        }),
      })
      .getByRole("button", { name: "处理任务", exact: true })
      .click();
    await expect(
      op.getByLabel("巡检检索名称", { exact: true }),
    ).not.toBeVisible();
    await op
      .getByLabel("巡检处理说明", { exact: true })
      .fill("尚未提交的现场整改反馈");
    op.once("dialog", (d) => d.dismiss());
    await op.getByRole("button", { name: "关闭任务详情", exact: true }).click();
    await expect(op.getByLabel("巡检处理说明", { exact: true })).toHaveText(
      "尚未提交的现场整改反馈",
    );
    op.once("dialog", (d) => d.dismiss());
    await op.getByRole("button", { name: "企业资料", exact: true }).click();
    await expect(op.getByLabel("巡检处理说明", { exact: true })).toBeVisible();
    await op.screenshot({
      path: "../../.backups/batch34-current-inspection-detail.png",
      fullPage: true,
    });
    op.once("dialog", (d) => d.accept());
    await returnToBusinessList(op, "巡检");
    await expect(op.getByLabel("巡检检索名称", { exact: true })).toHaveValue(
      value.name,
    );
    await expect(
      op.getByText("巡检第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await reg.getByRole("button", { name: "告警数据", exact: true }).click();
    await reg.getByLabel("告警检索名称", { exact: true }).fill(value.name);
    await reg
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      reg.getByText("告警第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await reg.getByRole("button", { name: "告警下一页", exact: true }).click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", {
          name: `ALERT-${value.event_ids[0]}`,
          exact: true,
        }),
      })
      .getByRole("button", { name: "告警详情", exact: true })
      .click();
    await reg
      .getByLabel("告警确认依据", { exact: true })
      .fill("未提交的确认依据");
    reg.once("dialog", (d) => d.dismiss());
    await reg
      .getByRole("button", { name: "关闭告警详情", exact: true })
      .click();
    await expect(reg.getByLabel("告警确认依据", { exact: true })).toHaveValue(
      "未提交的确认依据",
    );
    await reg.screenshot({
      path: "../../.backups/batch34-current-alert-detail.png",
      fullPage: true,
    });
    reg.once("dialog", (d) => d.accept());
    await returnToBusinessList(reg, "告警");
    await expect(
      reg.getByText("告警第2页 · 共25条", { exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    clean(value.station_id);
  }
});

test("subsidy workspaces separate policy and statistics while protecting the statement draft", async ({
  page,
}) => {
  const value = fixture("--operating");
  try {
    await login(page, "operator_a");
    await page.getByRole("button", { name: "补贴申报", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "补贴申请记录", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("补贴场站", { exact: true }),
    ).not.toBeVisible();
    await pickStation(page, "补贴场站", value);
    await page
      .getByLabel("补贴申请说明", { exact: true })
      .fill("校正验证申报说明");
    await page
      .getByRole("button", { name: "核算并创建草稿", exact: true })
      .click();
    await expect(
      page.getByLabel("修改补贴申请说明", { exact: true }),
    ).toHaveValue("校正验证申报说明");
    await page
      .getByLabel("修改补贴申请说明", { exact: true })
      .fill("暂未保存的申请说明");
    page.once("dialog", (d) => d.dismiss());
    await page
      .getByRole("button", { name: "关闭补贴详情", exact: true })
      .click();
    await expect(
      page.getByLabel("修改补贴申请说明", { exact: true }),
    ).toHaveValue("暂未保存的申请说明");
    page.once("dialog", (d) => d.dismiss());
    await page.getByRole("button", { name: "企业资料", exact: true }).click();
    await expect(
      page.getByLabel("修改补贴申请说明", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "../../.backups/batch34-current-subsidy-detail.png",
      fullPage: true,
    });
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button", { name: "关闭补贴详情", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "补贴申请记录", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "补贴统计", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "补贴统计", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("补贴检索名称", { exact: true }),
    ).not.toBeVisible();
    await page
      .getByRole("button", { name: "设备电量数据", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "设备日观测", exact: true }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      )
      .toBe(true);
  } finally {
    clean(value.station_id);
  }
});

test("rating details distinguish proposed and effective stars and preserve list return and score edits", async ({
  page,
}) => {
  const value = fixture("--operating", "--business-history", "25");
  try {
    await login(page, "operator_a");
    await page.getByRole("button", { name: "星级申报", exact: true }).click();
    await page.getByLabel("星级检索名称", { exact: true }).fill(value.name);
    await page
      .getByRole("button", { name: "查询星级记录", exact: true })
      .click();
    await expect(
      page.getByText("星级第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "星级下一页", exact: true }).click();
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", {
          name: `RATE-${value.rating_ids[0]}`,
          exact: true,
        }),
      })
      .getByRole("button", { name: "评星详情", exact: true })
      .click();
    await expect(
      page.getByLabel("星级检索名称", { exact: true }),
    ).not.toBeVisible();
    await expect(
      page.getByLabel("评审结果概况", { exact: true }),
    ).toContainText("当前有效星级");
    await returnToBusinessList(page, "星级");
    await expect(
      page.getByText("星级第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "评价结果与公示", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "星级公示查询", exact: true }),
    ).toBeVisible();
    await pickStation(page, "评星场站", value);
    await page
      .getByLabel("评星标准", { exact: true })
      .selectOption({ label: "演示星级标准（非正式标准） V1" });
    await page.getByLabel("自评-service", { exact: true }).fill("90");
    await page.getByLabel("自评-safety", { exact: true }).fill("90");
    await page
      .getByLabel("评星依据说明", { exact: true })
      .fill("评星页面校正验证依据");
    await page
      .getByRole("button", { name: "保存评星草稿", exact: true })
      .click();
    await page
      .getByLabel("修改评星依据", { exact: true })
      .fill("尚未保存的评星依据");
    page.once("dialog", (d) => d.dismiss());
    await page
      .getByRole("button", { name: "关闭评价详情", exact: true })
      .click();
    await expect(page.getByLabel("修改评星依据", { exact: true })).toHaveValue(
      "尚未保存的评星依据",
    );
    await page.screenshot({
      path: "../../.backups/batch34-current-rating-detail.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      )
      .toBe(true);
    await page.screenshot({
      path: "../../.backups/batch34-current-rating-390.png",
      fullPage: true,
    });
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button", { name: "关闭评价详情", exact: true })
      .click();
    await expect(
      page.getByLabel("星级检索名称", { exact: true }),
    ).toBeVisible();
  } finally {
    clean(value.station_id);
  }
});
