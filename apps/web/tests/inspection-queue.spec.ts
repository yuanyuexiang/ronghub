import { returnToBusinessList } from "./business-workspace";
import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("inspection overdue and severity filters reach older task details", async ({
  page,
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
        "--inspection-history",
        "25",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await page.getByRole("button", { name: "整改任务", exact: true }).click();
    await page.getByLabel("巡检检索名称").fill(fixture.name);
    await page.getByLabel("巡检筛选等级").selectOption("major");
    await page.getByLabel("巡检筛选状态").selectOption("assigned");
    await page.getByLabel("巡检筛选逾期").selectOption("true");
    await expect(
      page.getByRole("button", { name: "查询巡检记录", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      page.getByText("巡检第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "巡检下一页", exact: true }).click();
    await expect(
      page.getByText("巡检第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    const id = fixture.task_ids[0];
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: `TASK-${id}`, exact: true }),
      })
      .getByRole("button", { name: "处理任务", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: new RegExp(`^TASK-${id} ·`) }),
    ).toBeVisible();
    await returnToBusinessList(page, "巡检");
    await page.getByLabel("巡检检索编号").fill(String(id));
    await page
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      page.getByText("巡检第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("巡检筛选逾期").selectOption("false");
    await page
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      page.getByText("当前条件下没有巡检记录", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("巡检筛选逾期").selectOption("true");
    await page
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      page.getByText("巡检第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/inspection-queue.png",
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
