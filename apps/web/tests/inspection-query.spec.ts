import { returnToBusinessList } from "./business-workspace";
import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(count = 25) {
  return JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--inspection-history",
        String(count),
      ],
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
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}

test("inspection dates and feedback filters export the full applied query across pages and retain drafts on failure", async ({
  browser,
}) => {
  const value = fixture();
  const contexts = [
    await browser.newContext({ timezoneId: "America/New_York" }),
  ];
  try {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        `
import sys,json
from datetime import datetime,UTC
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal,engine
from app.models import Station,InspectionTask,InspectionAction,User
assert settings.demo_mode and engine.url.host in {'localhost','127.0.0.1'}
with SessionLocal.begin() as db:
 station=db.get(Station,int(sys.argv[1]));ids=json.loads(sys.argv[3])
 assert station and station.source=='browser-test' and station.name==sys.argv[2] and station.name.startswith('浏览器验收-')
 tasks=list(db.scalars(select(InspectionTask).where(InspectionTask.station_id==station.id)))
 assert {task.id for task in tasks}==set(ids)
 for task in tasks:
  task.created_at=datetime(2026,11,14,16,tzinfo=UTC)
  task.due_at=datetime(2026,11,16,0,tzinfo=UTC)
 db.get(InspectionTask,ids[0]).status='closed'
 # Seed historical missing facts only in this exact owned fixture.
 historical=db.get(InspectionTask,ids[1])
 executor=db.scalar(select(User.id).where(User.operator_id==historical.operator_id,User.role=='operator').order_by(User.id).limit(1))
 assert executor is not None
 historical.status='replied';historical.version=2
 db.add(InspectionAction(task_id=historical.id,actor_id=executor,action='reply',comment='Browser historical feedback with unknown user time'))
`,
        String(value.station_id),
        value.name,
        JSON.stringify(value.task_ids),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    const reg = await contexts[0].newPage();
    await login(reg, "regulator");
    await navigateWorkspace(reg, "巡检任务管理");
    await reg.getByLabel("巡检检索名称", { exact: true }).fill(value.name);
    await reg
      .getByLabel("巡检创建开始日期", { exact: true })
      .fill("2026-11-15");
    await reg
      .getByLabel("巡检创建结束日期", { exact: true })
      .fill("2026-11-15");
    await reg
      .getByLabel("巡检反馈状态", { exact: true })
      .selectOption("not_reported");
    await reg
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      reg.getByText("巡检第1页 · 共23条", { exact: true }),
    ).toBeVisible();
    await reg.getByRole("button", { name: "巡检下一页", exact: true }).click();
    await expect(
      reg.getByText("巡检第2页 · 共23条", { exact: true }),
    ).toBeVisible();
    await reg
      .getByLabel("巡检检索名称", { exact: true })
      .fill("尚未应用的查询草稿");
    await reg
      .getByLabel("巡检创建结束日期", { exact: true })
      .fill("2026-11-16");
    await reg
      .getByLabel("巡检创建开始日期", { exact: true })
      .fill("2026-11-16");
    await reg
      .getByLabel("巡检反馈状态", { exact: true })
      .selectOption("reported");
    await reg.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(reg.getByLabel("巡检检索名称", { exact: true })).toHaveValue(
      "尚未应用的查询草稿",
    );
    await expect(
      reg.getByLabel("巡检创建开始日期", { exact: true }),
    ).toHaveValue("2026-11-16");
    await expect(
      reg.getByText("巡检第2页 · 共23条", { exact: true }),
    ).toBeVisible();
    const response = reg.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/inspections/export?") &&
        r.request().method() === "GET",
    );
    const downloading = reg.waitForEvent("download");
    await reg
      .getByRole("button", { name: "导出巡检任务 CSV", exact: true })
      .click();
    const downloaded = await downloading,
      exported = await response;
    expect(exported.status()).toBe(200);
    const query = new URL(exported.url()).searchParams;
    expect(query.get("q")).toBe(value.name);
    expect(query.get("created_from")).toBe("2026-11-15");
    expect(query.get("created_to")).toBe("2026-11-15");
    expect(query.get("execution_state")).toBe("not_reported");
    expect(query.has("page")).toBe(false);
    expect(query.has("page_size")).toBe(false);
    expect(downloaded.suggestedFilename()).toBe("ronghub-inspections.csv");
    const content = readFileSync((await downloaded.path())!, "utf8");
    expect(content.startsWith("\ufeff")).toBe(true);
    const lines = content.trim().split(/\r?\n/);
    expect(lines).toHaveLength(24);
    const ids = lines.slice(1).map((line) => Number(line.split(",")[0]));
    expect(new Set(ids).size).toBe(23);
    expect(ids).not.toContain(value.task_ids[0]);
    expect(ids).not.toContain(value.task_ids[1]);
    const downloadRegion = reg.getByRole("region", {
      name: "巡检任务导出",
      exact: true,
    });
    await reg.route("**/api/v1/inspections/export**", (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模拟导出范围过大，请缩小范围" }),
      }),
    );
    let unexpected = false;
    const failureListener = () => {
      unexpected = true;
    };
    reg.on("download", failureListener);
    await reg
      .getByRole("button", { name: "导出巡检任务 CSV", exact: true })
      .click();
    await expect(downloadRegion.getByRole("alert")).toContainText(
      "模拟导出范围过大",
    );
    expect(unexpected).toBe(false);
    reg.off("download", failureListener);
    await reg.unroute("**/api/v1/inspections/export**");
    await reg
      .getByRole("button", { name: "重置巡检筛选", exact: true })
      .click();
    await expect(downloadRegion.getByRole("alert")).toHaveCount(0);
    await expect(
      reg.getByLabel("巡检创建开始日期", { exact: true }),
    ).toHaveValue("");
    await expect(
      reg.getByLabel("巡检创建结束日期", { exact: true }),
    ).toHaveValue("");
    await expect(reg.getByLabel("巡检反馈状态", { exact: true })).toHaveValue(
      "",
    );
    await returnToBusinessList(reg, "巡检");
    await reg
      .getByLabel("巡检检索编号", { exact: true })
      .fill(String(value.task_ids[0]));
    await reg
      .getByLabel("巡检反馈状态", { exact: true })
      .selectOption("unknown");
    await reg
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    const table = reg.getByRole("table", { name: "巡检任务列表", exact: true }),
      unknown = table.getByRole("row").filter({
        has: reg.getByRole("cell", {
          name: `TASK-${value.task_ids[0]}`,
          exact: true,
        }),
      });
    await expect(unknown).toContainText("历史未留存");
    await expect(unknown).toContainText("填报：未留存");
    await returnToBusinessList(reg, "巡检");
    await reg
      .getByLabel("巡检检索编号", { exact: true })
      .fill(String(value.task_ids[1]));
    await reg
      .getByLabel("巡检反馈状态", { exact: true })
      .selectOption("reported");
    await reg
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    const reported = table.getByRole("row").filter({
      has: reg.getByRole("cell", {
        name: `TASK-${value.task_ids[1]}`,
        exact: true,
      }),
    });
    await expect(reported).toContainText("已反馈");
    await expect(reported).toContainText("填报：未留存");
    await expect(reported).toContainText(/2026\/11\/16 0?8:00:00/);
    await reg.screenshot({
      path: "test-results/inspection-query-desktop.png",
      fullPage: true,
    });
    await reg.setViewportSize({ width: 390, height: 844 });
    expect(
      await reg.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    clean(value.station_id);
  }
});

test("inspection read and reply staff cannot download without independent export permission", async ({
  page,
}) => {
  const value = fixture(1),
    actor = JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.permission_fixture",
          "create",
          "--module",
          "inspections",
          "--kind",
          "reply",
        ],
        { cwd: "../server", encoding: "utf8" },
      ),
    );
  try {
    await login(page, actor.username);
    await navigateWorkspace(page, "整改任务");
    await returnToBusinessList(page, "巡检");
    await page
      .getByLabel("巡检检索编号", { exact: true })
      .fill(String(value.task_ids[0]));
    await page
      .getByRole("button", { name: "查询巡检记录", exact: true })
      .click();
    await expect(
      page.getByRole("cell", {
        name: `TASK-${value.task_ids[0]}`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "导出巡检任务 CSV", exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await page.request.get(
          `/api/v1/inspections/export?record_id=${value.task_ids[0]}`,
        )
      ).status(),
    ).toBe(403);
  } finally {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.permission_fixture",
        "clean",
        "--user",
        String(actor.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    clean(value.station_id);
  }
});
