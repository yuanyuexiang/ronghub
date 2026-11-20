import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes, randomInt } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function pageFixtures(id: number, name: string, create = false) {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      `
import sys
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal, engine
from app.models import StaffApplication
assert settings.demo_mode and engine.url.host in {'localhost','127.0.0.1'}
with SessionLocal.begin() as db:
    row=db.get(StaffApplication,int(sys.argv[1]))
    assert row and row.is_demo and row.applicant_name==sys.argv[2] and row.applicant_name.startswith('浏览器工作人员-')
    others=list(db.scalars(select(StaffApplication).where(StaffApplication.applicant_name==row.applicant_name,StaffApplication.id!=row.id)))
    assert all(item.is_demo and item.user_id is None and item.status=='pending' for item in others)
    if sys.argv[3]=='create':
        assert not others
        for index in range(20):
            db.add(StaffApplication(requested_role=row.requested_role,applicant_name=row.applicant_name,contact_phone=f"138{(row.id*20+index)%100000000:08d}",is_demo=True))
    else:
        for item in others: db.delete(item)
`,
      String(id),
      name,
      create ? "create" : "clean",
    ],
    { cwd: "../server", stdio: "pipe" },
  );
}
async function login(page: Page, username: string, secret: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
}
test("staff applicant gains no account until explicit scoped approval and must replace temporary password", async ({
  browser,
}) => {
  const key = randomBytes(8).toString("hex"),
    name = "浏览器工作人员-" + key,
    profileName = "browser-staff-role-" + key;
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  let applicationId: number | undefined, profileId: number | undefined;
  try {
    const [applicant, reg] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await applicant.goto("/staff-register");
    await applicant
      .getByLabel("工作人员申请身份", { exact: true })
      .selectOption("association");
    await applicant.getByLabel("工作人员申请姓名", { exact: true }).fill(name);
    await applicant
      .getByLabel("工作人员申请联系电话", { exact: true })
      .fill("139" + String(randomInt(10000000, 99999999)));
    const created = applicant.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/auth/staff-enrollment") &&
        r.request().method() === "POST",
    );
    await applicant
      .getByRole("button", { name: "提交工作人员账号申请", exact: true })
      .click();
    const submitted = await created;
    expect(submitted.status()).toBe(201);
    applicationId = (await submitted.json()).id;
    pageFixtures(applicationId!, name, true);
    await expect(
      applicant.getByRole("heading", { name: "等待人工审核", exact: true }),
    ).toBeVisible();
    expect((await applicant.request.get("/api/v1/auth/me")).status()).toBe(401);
    await login(reg, "regulator", password);
    await expect(
      reg.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    const me = await (await reg.request.get("/api/v1/auth/me")).json();
    const profile = await reg.request.post("/api/v1/roles", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        name: profileName,
        base_role: "association",
        permissions: ["stations.read", "content.read"],
      },
    });
    expect(profile.status()).toBe(201);
    profileId = (await profile.json()).id;
    await navigateWorkspace(reg, "人员账号申请");
    await reg.getByLabel("人员申请关键词", { exact: true }).fill(name);
    await reg
      .getByRole("button", { name: "查询人员申请", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "下一页人员申请", exact: true })
      .click();
    const row = reg.getByRole("row").filter({
      has: reg.getByRole("cell", {
        name: `STAFF-${applicationId}`,
        exact: true,
      }),
    });
    await row
      .getByRole("button", { name: "查看人员申请", exact: true })
      .click();
    await reg.getByLabel("申请审核查找角色", { exact: true }).fill(profileName);
    await reg
      .getByRole("button", { name: "筛选申请审核角色", exact: true })
      .click();
    await reg
      .getByRole("radio", { name: `分配申请角色：${profileName}`, exact: true })
      .check();
    const username = "staff-browser-" + key,
      temporary = "Temp-Staff-" + randomBytes(12).toString("hex");
    await reg.getByLabel("审核开通账号", { exact: true }).fill(username);
    await reg.getByLabel("审核临时密码", { exact: true }).fill(temporary);
    await reg
      .getByLabel("审核账号区域范围", { exact: true })
      .selectOption("districts");
    await reg.getByLabel("审核指定区域", { exact: true }).fill("鼓楼区");
    await reg
      .getByLabel("人员申请审核意见", { exact: true })
      .fill("已核验身份，初始凭据另行交付");
    pageFixtures(applicationId!, name);
    await reg.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(reg.getByLabel("审核开通账号", { exact: true })).toHaveValue(
      username,
    );
    await expect(reg.getByLabel("审核临时密码", { exact: true })).toHaveValue(
      temporary,
    );
    await expect(reg.getByLabel("审核指定区域", { exact: true })).toHaveValue(
      "鼓楼区",
    );
    const decision = reg.waitForResponse(
      (r) =>
        r.url().endsWith(`/staff-applications/${applicationId}/review`) &&
        r.request().method() === "POST",
    );
    await reg
      .getByRole("button", { name: "审核通过并开通账号", exact: true })
      .click();
    const reviewed = await decision;
    expect(reviewed.status()).toBe(200);
    expect(await reviewed.text()).not.toContain(temporary);
    await expect(row).toContainText("已开通");
    await applicant
      .getByRole("button", { name: "刷新本人申请进度", exact: true })
      .click();
    await expect(applicant.getByText(username, { exact: true })).toBeVisible();
    expect(await applicant.locator("body").innerText()).not.toContain(
      temporary,
    );
    await row
      .getByRole("button", { name: "查看人员申请", exact: true })
      .click();
    await expect(
      reg.getByText("审核账号：regulator", { exact: true }),
    ).toBeVisible();
    await reg.screenshot({
      path: "test-results/staff-application-review.png",
      fullPage: true,
    });
    await login(applicant, username, temporary);
    await expect(
      applicant.getByRole("heading", {
        name: "首次登录请修改临时密码",
        exact: true,
      }),
    ).toBeVisible();
    expect((await applicant.request.get("/api/v1/stations")).status()).toBe(
      403,
    );
    const finalPassword = "Staff-Final-" + randomBytes(12).toString("hex");
    await applicant.getByLabel("本人当前密码", { exact: true }).fill(temporary);
    await applicant
      .getByLabel("本人新密码", { exact: true })
      .fill(finalPassword);
    await applicant
      .getByLabel("确认本人新密码", { exact: true })
      .fill(finalPassword);
    await applicant
      .getByRole("button", { name: "更新密码并重新登录", exact: true })
      .click();
    await expect(
      applicant.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    expect(
      (
        await applicant.request.get("/api/v1/auth/staff-enrollment/me")
      ).status(),
    ).toBe(401);
    await login(applicant, username, finalPassword);
    await expect(
      applicant.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    const identity = await (
      await applicant.request.get("/api/v1/auth/me")
    ).json();
    expect(identity.role).toBe("association");
    expect(identity.allowed_districts).toEqual(["鼓楼区"]);
    expect(identity.permissions).toEqual(["content.read", "stations.read"]);
    expect(
      (await applicant.request.get("/api/v1/staff-applications")).status(),
    ).toBe(403);
    expect(
      (await (await applicant.request.get("/api/v1/auth/phone")).json())
        .verified,
    ).toBe(false);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    if (applicationId) pageFixtures(applicationId, name);
    if (applicationId || profileId)
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.staff_application_fixture",
          ...(applicationId ? ["--application", String(applicationId)] : []),
          ...(profileId ? ["--profile", String(profileId)] : []),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
  }
});
