import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
type Station = { station_id: number; name: string };
function stations(group: string, count = 1): Station[] {
  const value = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--group-name",
        group,
        "--count",
        String(count),
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  return count === 1 ? [value] : value.items;
}
function cleanup(items: Station[], plan?: { id: number; key: string }) {
  if (plan)
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.inspection_plan_fixture",
        "--plan",
        String(plan.id),
        "--request-key",
        plan.key,
        "--stations",
        ...items.map((s) => String(s.station_id)),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  for (const row of items)
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "clean",
        "--station",
        String(row.station_id),
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
async function create(page: Page) {
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/inspection-plans") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "新建巡检计划", exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(201);
  return {
    id: (await result.json()).id as number,
    key: String(result.request().postDataJSON().request_key),
  };
}

test("private cross-page inspection draft preserves local edits and replays a lost publication receipt without duplicate tasks", async ({
  browser,
}) => {
  test.setTimeout(60_000); // Three roles, 21 owned station fixtures and explicit cleanup; action deadlines remain unchanged.
  const group = "plan-" + randomBytes(5).toString("hex"),
    items = stations(group, 21),
    title = "浏览器巡检计划-" + group;
  const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext()));
  let plan: { id: number; key: string } | undefined;
  try {
    const [reg, owner, outside] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await login(reg, "regulator");
    await login(owner, "operator_a");
    await login(outside, "operator_b");
    await reg.getByRole("button", { name: "巡检督办", exact: true }).click();
    plan = await create(reg);
    const region = reg.getByRole("region", {
        name: "巡检计划与批量下发",
        exact: true,
      }),
      detail = region.getByRole("region", {
        name: "巡检计划详情",
        exact: true,
      });
    expect(
      (await owner.request.get(`/api/v1/inspection-plans/${plan.id}`)).status(),
    ).toBe(403);
    await detail.getByLabel("计划任务名称", { exact: true }).fill(title);
    await detail
      .getByLabel("计划巡检要求", { exact: true })
      .fill("请完成设备现场巡检并提交证明材料");
    const picker = detail.getByRole("region", {
      name: "计划目标选择",
      exact: true,
    });
    await picker.getByLabel("计划场站筛选", { exact: true }).fill(group);
    await picker
      .getByRole("button", { name: "筛选计划场站", exact: true })
      .click();
    await expect(picker).toHaveAttribute("aria-busy", "false");
    await expect(picker.getByRole("checkbox")).toHaveCount(20);
    await picker
      .getByRole("checkbox", {
        name: `选择 ${items[0].name}（场站${items[0].station_id}）`,
        exact: true,
      })
      .check();
    await picker
      .getByRole("button", { name: "计划场站下一页", exact: true })
      .click();
    await expect(picker.getByRole("checkbox")).toHaveCount(1);
    await picker
      .getByRole("checkbox", {
        name: `选择 ${items[20].name}（场站${items[20].station_id}）`,
        exact: true,
      })
      .check();
    const chosen = detail.getByRole("region", {
      name: "已选计划场站",
      exact: true,
    });
    await expect(chosen).toContainText(items[0].name);
    await expect(chosen).toContainText(items[20].name);
    await detail
      .getByLabel("计划巡检要求", { exact: true })
      .fill("未保存的最终巡检要求");
    await region
      .getByLabel("计划标题筛选", { exact: true })
      .fill("尚未应用的列表筛选");
    await reg.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(detail.getByLabel("计划巡检要求", { exact: true })).toHaveText(
      "未保存的最终巡检要求",
    );
    await expect(
      region.getByLabel("计划标题筛选", { exact: true }),
    ).toHaveValue("尚未应用的列表筛选");
    await expect(chosen).toContainText(items[0].name);
    await expect(chosen).toContainText(items[20].name);
    await expect(
      detail.getByRole("button", { name: "批量下发巡检", exact: true }),
    ).toBeDisabled();
    const saved = reg.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/v1/inspection-plans/${plan!.id}`) &&
        r.request().method() === "PUT",
    );
    await detail
      .getByRole("button", { name: "保存计划草稿", exact: true })
      .click();
    const savedResponse = await saved;
    expect(savedResponse.status()).toBe(200);
    expect((await savedResponse.json()).due_at).toBeNull();
    await expect(detail).toContainText("内容已保存");
    const before = await owner.request.get("/api/v1/inspections", {
      params: { q: title },
    });
    expect((await before.json()).total).toBe(0);
    await detail
      .getByRole("button", { name: "关闭计划详情", exact: true })
      .click();
    await region
      .getByRole("button", { name: `打开计划 PLAN-${plan.id}`, exact: true })
      .click();
    await expect(detail.getByLabel("计划巡检要求", { exact: true })).toHaveText(
      "未保存的最终巡检要求",
    );
    await expect(
      detail.getByLabel("计划任务期限", { exact: true }),
    ).toHaveValue("");
    let firstReceipt: { targets: { task_id: number }[] } | undefined,
      lost = false;
    const publishUrl = `**/api/v1/inspection-plans/${plan.id}/publish`;
    await reg.route(publishUrl, async (route) => {
      if (!lost) {
        lost = true;
        const response = await route.fetch();
        expect(response.status()).toBe(200);
        firstReceipt = await response.json();
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "模拟下发回执丢失" }),
        });
      } else await route.continue();
    });
    await detail
      .getByRole("button", { name: "批量下发巡检", exact: true })
      .click();
    await expect(region.getByRole("alert")).toContainText("模拟下发回执丢失");
    expect(
      (
        await (
          await owner.request.get("/api/v1/inspections", {
            params: { q: title },
          })
        ).json()
      ).total,
    ).toBe(2);
    const repeated = reg.waitForResponse(
      (r) =>
        r.url().endsWith(`/inspection-plans/${plan!.id}/publish`) &&
        r.request().method() === "POST",
    );
    await detail
      .getByRole("button", { name: "批量下发巡检", exact: true })
      .click();
    const replay = await repeated;
    expect(replay.status()).toBe(200);
    const receipt = await replay.json();
    expect(receipt.targets.map((t: { task_id: number }) => t.task_id)).toEqual(
      firstReceipt!.targets.map((t) => t.task_id),
    );
    await expect(detail).toContainText("已下发2站");
    await reg.unroute(publishUrl);
    expect(
      (
        await (
          await owner.request.get("/api/v1/inspections", {
            params: { q: title },
          })
        ).json()
      ).total,
    ).toBe(2);
    for (const target of receipt.targets) {
      const proof = await owner.request.get(
        `/api/v1/inspections/${target.task_id}`,
      );
      expect(proof.status()).toBe(200);
      const value = await proof.json();
      expect(value.plan_id).toBe(plan.id);
      expect(value.due_at).toBeNull();
      expect(value.overdue).toBe(false);
      expect(
        (
          await outside.request.get(`/api/v1/inspections/${target.task_id}`)
        ).status(),
      ).toBe(404);
    }
    await detail.screenshot({
      path: "test-results/inspection-plan-desktop.png",
    });
    await detail
      .getByRole("button", {
        name: new RegExp(`办理任务 TASK-${receipt.targets[0].task_id} ·`),
      })
      .click();
    await expect(
      reg.getByRole("heading", {
        name: `TASK-${receipt.targets[0].task_id} · ${title}`,
        exact: true,
      }),
    ).toBeVisible();
    await reg.setViewportSize({ width: 390, height: 844 });
    expect(
      await reg.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await detail.screenshot({
      path: "test-results/inspection-plan-mobile.png",
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    cleanup(items, plan);
  }
});

test("partial plan can clear rich content, protect unsaved navigation and delete without publishing", async ({
  page,
}) => {
  const group = "partial-" + randomBytes(5).toString("hex"),
    items = stations(group);
  let plan: { id: number; key: string } | undefined;
  try {
    await login(page, "regulator");
    await page.getByRole("button", { name: "巡检督办", exact: true }).click();
    plan = await create(page);
    const region = page.getByRole("region", {
        name: "巡检计划与批量下发",
        exact: true,
      }),
      detail = region.getByRole("region", {
        name: "巡检计划详情",
        exact: true,
      });
    await detail
      .getByLabel("计划任务名称", { exact: true })
      .fill("浏览器巡检计划-" + group);
    await detail
      .getByLabel("计划巡检要求", { exact: true })
      .fill("可以先保存的要求");
    await detail
      .getByRole("button", { name: "保存计划草稿", exact: true })
      .click();
    await expect(detail).toContainText("内容已保存");
    await detail.getByLabel("计划任务名称", { exact: true }).fill("");
    const requirementEditor = detail.getByLabel("计划巡检要求", {
      exact: true,
    });
    await requirementEditor.click();
    await requirementEditor.press("ControlOrMeta+a");
    await requirementEditor.press("Backspace");
    await expect(requirementEditor).toHaveText("");
    const cleared = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/v1/inspection-plans/${plan!.id}`) &&
        r.request().method() === "PUT",
    );
    await detail
      .getByRole("button", { name: "保存计划草稿", exact: true })
      .click();
    const clearedResponse = await cleared;
    expect(clearedResponse.status()).toBe(200);
    expect(clearedResponse.request().postDataJSON().description).toBe("");
    expect(clearedResponse.request().postDataJSON().rich_document).toBeNull();
    await expect(detail).toContainText("内容已保存");
    await expect(detail.getByLabel("计划巡检要求", { exact: true })).toHaveText(
      "",
    );
    await detail
      .getByLabel("计划任务名称", { exact: true })
      .fill("尚未保存的内容");
    await region
      .getByRole("button", { name: "新建巡检计划", exact: true })
      .click();
    await expect(region.getByRole("alert")).toContainText("未保存修改");
    await region
      .getByRole("button", { name: "保留当前内容", exact: true })
      .click();
    await expect(
      detail.getByLabel("计划任务名称", { exact: true }),
    ).toHaveValue("尚未保存的内容");
    await detail
      .getByRole("button", { name: "重新载入已保存计划", exact: true })
      .click();
    await region
      .getByRole("button", { name: "放弃修改并继续", exact: true })
      .click();
    await expect(
      detail.getByLabel("计划任务名称", { exact: true }),
    ).toHaveValue("");
    await expect(detail.getByLabel("计划巡检要求", { exact: true })).toHaveText(
      "",
    );
    await detail
      .getByRole("button", { name: "删除计划草稿", exact: true })
      .click();
    await region
      .getByRole("button", { name: "确认删除草稿", exact: true })
      .click();
    await expect(detail).toContainText("已删除");
    const current = await (
      await page.request.get(`/api/v1/inspection-plans/${plan.id}`)
    ).json();
    expect(current.published_count).toBe(0);
    expect(current.status).toBe("deleted");
  } finally {
    cleanup(items, plan);
  }
});

test("inspection assigner uses scoped candidates without gaining station profile read", async ({
  page,
}) => {
  const items = stations("limited-" + randomBytes(5).toString("hex"));
  const actor = JSON.parse(
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
        "assign",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        `
import sys
from app.config import settings
from app.db import SessionLocal,engine
from app.models import User,RoleProfile
assert settings.demo_mode and engine.url.host in {'localhost','127.0.0.1'}
with SessionLocal.begin() as db:
 user=db.get(User,int(sys.argv[1]));profile=db.get(RoleProfile,int(sys.argv[2]))
 assert user and user.username==sys.argv[3] and user.username.startswith('browser-permission-') and user.role=='regulator' and user.role_profile_id==profile.id and profile.name==user.username
 profile.permissions=['inspections.read','inspections.assign']
`,
        String(actor.id),
        String(actor.profile_id),
        actor.username,
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    await login(page, actor.username);
    expect((await page.request.get("/api/v1/stations")).status()).toBe(403);
    await page.getByRole("button", { name: "巡检督办", exact: true }).click();
    const single = page.locator("section.panel").filter({
      has: page.getByRole("heading", { name: "单站快捷下发", exact: true }),
    });
    await single
      .getByLabel("巡检场站名称筛选", { exact: true })
      .fill(items[0].name);
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/inspection-plans/stations?") &&
        r.request().method() === "GET",
    );
    await single.getByRole("button", { name: "筛选", exact: true }).click();
    expect((await response).status()).toBe(200);
    await expect(single.getByLabel("巡检场站", { exact: true })).toBeEnabled();
    await single
      .getByLabel("巡检场站", { exact: true })
      .selectOption(String(items[0].station_id));
    await expect(single.getByLabel("巡检场站", { exact: true })).toHaveValue(
      String(items[0].station_id),
    );
    expect((await page.request.get("/api/v1/stations")).status()).toBe(403);
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
    cleanup(items);
  }
});
