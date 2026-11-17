import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  return {
    "x-csrf-token": (await (await page.request.get("/api/v1/auth/me")).json())
      .csrf_token,
  };
}
test("rejected replacement retains the first submitted file and immutable contact while resubmitting a new version", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--facilities"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const owner = await browser.newContext(),
    reviewer = await browser.newContext();
  try {
    const op = await owner.newPage(),
      reg = await reviewer.newPage();
    const headers = await login(op, "operator_a"),
      regHeaders = await login(reg, "regulator");
    const created = await op.request.post("/api/v1/applications", {
      headers,
      data: {
        kind: "access",
        station_id: fixture.station_id,
        payload: {
          note: "first submitted statement",
          contact: { name: "first applicant", phone: "13800000001" },
        },
      },
    });
    expect(created.status()).toBe(201);
    const application = await created.json(),
      path = `/api/v1/applications/${application.id}`;
    const uploaded = await op.request.post(path + "/attachments", {
      headers,
      multipart: {
        file: {
          name: "first-proof.pdf",
          mimeType: "application/pdf",
          buffer: Buffer.from("%PDF-1.4\nfirst immutable file"),
        },
      },
    });
    expect(uploaded.status()).toBe(201);
    const file = await uploaded.json();
    const context = await (
      await op.request.get(path + "/review-context")
    ).json();
    expect(
      (
        await op.request.post(path + "/actions", {
          headers,
          data: {
            action: "submit",
            version: 1,
            review_token: context.review_token,
          },
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await reg.request.post(path + "/actions", {
          headers: regHeaders,
          data: {
            action: "reject",
            version: 2,
            comment: "replace supporting document",
          },
        })
      ).status(),
    ).toBe(200);
    await op.getByRole("button", { name: "场站申请", exact: true }).click();
    await op
      .getByLabel("检索申请编号", { exact: true })
      .fill(String(application.id));
    await op.getByRole("button", { name: "查询申请", exact: true }).click();
    await op
      .getByRole("row")
      .filter({ hasText: `APP-${application.id}` })
      .getByRole("button", { name: "查看处理", exact: true })
      .click();
    const evidence = op.getByRole("region", {
      name: "申请送审资料",
      exact: true,
    });
    await expect(evidence).toContainText(fixture.name);
    await op
      .getByLabel("申请联系人", { exact: true })
      .fill("replacement applicant");
    await op.getByRole("button", { name: "保存申请信息", exact: true }).click();
    await expect(
      op.getByRole("button", { name: "提交审核", exact: true }),
    ).toBeEnabled();
    await op.getByRole("button", { name: "删除", exact: true }).click();
    await expect(op.getByText("尚未上传材料", { exact: true })).toBeVisible();
    expect(
      (
        await op.request.get(`/api/v1/attachments/${file.id}/download`)
      ).status(),
    ).toBe(404);
    await op.getByLabel("上传申请材料", { exact: true }).setInputFiles({
      name: "replacement-proof.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nreplacement file"),
    });
    await expect(
      op.getByRole("link", { name: "replacement-proof.pdf", exact: true }),
    ).toBeVisible();
    await op.getByRole("button", { name: "提交审核", exact: true }).click();
    await expect(
      op.getByRole("row").filter({ hasText: `APP-${application.id}` }),
    ).toContainText("待审核");
    await evidence
      .getByRole("button", { name: "历次送审版本", exact: true })
      .click();
    await expect(
      evidence.getByRole("button", { name: /^记录版本 / }),
    ).toHaveCount(2);
    await evidence.getByRole("button", { name: /^记录版本 2 ·/ }).click();
    const frozen = evidence.getByRole("article", {
      name: "冻结送审内容",
      exact: true,
    });
    await expect(frozen).toContainText("first applicant");
    await expect(frozen).not.toContainText("replacement applicant");
    const link = frozen.getByRole("link", {
      name: "first-proof.pdf",
      exact: true,
    });
    await expect(link).toBeVisible();
    const evidenceBox = await evidence.boundingBox();
    expect(evidenceBox!.width).toBeGreaterThan(900);
    await evidence.screenshot({
      path: "test-results/application-evidence-desktop.png",
    });
    await op.setViewportSize({ width: 390, height: 844 });
    await expect(
      evidence.getByRole("article", { name: "冻结送审内容", exact: true }),
    ).toBeVisible();
    expect(
      await op.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await evidence.screenshot({
      path: "test-results/application-evidence-mobile.png",
    });
    const href = await link.getAttribute("href");
    expect(href).toBeTruthy();
    const historical = await op.request.get(href!);
    expect(historical.status()).toBe(200);
    expect((await historical.body()).toString()).toBe(
      "%PDF-1.4\nfirst immutable file",
    );
  } finally {
    await owner.close();
    await reviewer.close();
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
