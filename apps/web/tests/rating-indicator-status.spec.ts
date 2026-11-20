import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { pickStation } from "./station";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "运营总览", exact: true }),
  ).toBeVisible();
}
async function token(page: Page) {
  return (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token;
}
function cleanup(code: string) {
  execFileSync(
    "uv",
    ["run", "python", "-m", "app.rating_standard_fixture", "--code", code],
    { cwd: "../server" },
  );
}
test("indicator-level activation previews real denominators and new versions never rewrite the original report", async ({
  browser,
}) => {
  const code = "browser-config-" + randomBytes(8).toString("hex"),
    name = "指标状态 " + code;
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const association = await browser.newContext(),
    operator = await browser.newContext();
  try {
    const ass = await association.newPage(),
      op = await operator.newPage();
    await login(ass, "association");
    await ass.getByRole("button", { name: "星级评价", exact: true }).click();
    await ass
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    let form = ass.locator("form.standard-builder");
    await form.getByLabel("标准代码", { exact: true }).fill(code);
    await form.getByLabel("标准名称", { exact: true }).fill(name);
    const today = await form
      .getByLabel("指标1生效日期", { exact: true })
      .inputValue();
    const later = new Date(today + "T00:00:00Z");
    later.setUTCDate(later.getUTCDate() + 30);
    await form
      .getByLabel("指标2状态", { exact: true })
      .selectOption("disabled");
    await form
      .getByLabel("指标2生效日期", { exact: true })
      .fill(later.toISOString().slice(0, 10));
    await form
      .getByRole("button", { name: "预览参与指标与权重", exact: true })
      .click();
    let preview = form.getByRole("region", {
      name: "指标参与权重预览",
      exact: true,
    });
    await expect(
      preview.getByRole("row").filter({ hasText: "服务材料" }),
    ).toContainText("100.0000%");
    await expect(
      preview.getByRole("row").filter({ hasText: "安全材料" }),
    ).toContainText("停用，不参与");
    const listed = await (
      await ass.request.get("/api/v1/ratings/standards")
    ).json();
    expect(
      listed.items.some((row: { code: string }) => row.code === code),
    ).toBe(false);
    await form.getByLabel("指标1weight", { exact: true }).fill("30");
    await expect(preview).toHaveCount(0);
    await form
      .getByRole("button", { name: "预览参与指标与权重", exact: true })
      .click();
    preview = form.getByRole("region", {
      name: "指标参与权重预览",
      exact: true,
    });
    await expect(preview).toBeVisible();
    await form.screenshot({
      path: "test-results/indicator-active-weight-preview.png",
    });
    const saved = ass.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standards") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const first = await (await saved).json();
    expect(first.rules.indicator_status_version).toBe(1);
    expect(first.activation_state).toBe("effective");
    expect(first.effective_on).toBe(today);
    await login(op, "operator_a");
    await op.getByRole("button", { name: "星级申报", exact: true }).click();
    await op
      .getByLabel("评星标准", { exact: true })
      .selectOption(String(first.id));
    await pickStation(op, "评星场站", fixture);
    await op.getByLabel("自评-service", { exact: true }).fill("95");
    await expect(op.getByLabel("自评-safety", { exact: true })).toHaveCount(0);
    await op
      .getByLabel("评星依据说明", { exact: true })
      .fill("启用项评分与旧版冻结验证");
    const applying = op.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/applications") &&
        r.request().method() === "POST",
    );
    await op.getByRole("button", { name: "保存评星草稿", exact: true }).click();
    const claim = await (await applying).json();
    expect(claim.calculation.total).toBe("100.00");
    expect(claim.calculation.excluded).toEqual(["safety"]);
    const path = `/api/v1/ratings/applications/${claim.id}`;
    const original = await (await op.request.get(path)).json();
    await expect(op.getByText(/安全材料 · 已停用/)).toBeVisible();
    await ass.getByText(`查看指标配置 ${name} V1`, { exact: true }).click();
    await ass
      .getByRole("button", { name: `基于此版新建 ${code} V2`, exact: true })
      .click();
    form = ass.locator("form.standard-builder");
    await expect(form.getByLabel("指标2状态", { exact: true })).toHaveValue(
      "disabled",
    );
    await form.getByLabel("指标2状态", { exact: true }).selectOption("enabled");
    await form.getByLabel("指标2生效日期", { exact: true }).fill(today);
    await form
      .getByRole("button", { name: "预览参与指标与权重", exact: true })
      .click();
    preview = form.getByRole("region", {
      name: "指标参与权重预览",
      exact: true,
    });
    await expect(
      preview.getByRole("row").filter({ hasText: "服务材料" }),
    ).toContainText("42.8571%");
    await expect(
      preview.getByRole("row").filter({ hasText: "安全材料" }),
    ).toContainText("57.1429%");
    const nextSaved = ass.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/ratings/standards") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const second = await (await nextSaved).json();
    expect(second.version).toBe(2);
    expect((await (await op.request.get(path)).json()).rule_snapshot).toEqual(
      original.rule_snapshot,
    );
    const ot = await token(op),
      at = await token(ass);
    const action = async (
      actor: Page,
      csrf: string,
      action: string,
      version: number,
      extra = {},
    ) =>
      actor.request.post(path + "/actions", {
        headers: { "x-csrf-token": csrf },
        data: { action, version, comment: "验证指标参与快照", ...extra },
      });
    expect((await action(op, ot, "submit", 1)).status()).toBe(200);
    expect(
      (
        await action(ass, at, "review", 2, {
          scores: { service: "95", safety: "0" },
        })
      ).status(),
    ).toBe(422);
    expect(
      (
        await action(ass, at, "review", 2, { scores: { service: "95" } })
      ).status(),
    ).toBe(200);
    expect(
      (
        await action(ass, at, "publish", 3, {
          valid_until: later.toISOString().slice(0, 10),
        })
      ).status(),
    ).toBe(200);
    const report = await (await ass.request.get(path + "/report")).text();
    expect(report).toContain("停用，不参与");
    expect(report).toContain("<td>不参与</td>");
    expect(report).toContain("启用权重之和");
    const reassessed = await op.request.post("/api/v1/ratings/applications", {
      headers: { "x-csrf-token": ot },
      data: {
        station_id: fixture.station_id,
        standard_id: second.id,
        kind: "reassessment",
        parent_id: claim.id,
        parent_version: 4,
        evidence_note: "使用新版本启用状态复评",
        scores: { service: "95", safety: "0" },
      },
    });
    expect(reassessed.status()).toBe(201);
    expect((await reassessed.json()).calculation.total).toBe("65.71");
    expect((await (await op.request.get(path)).json()).calculation.total).toBe(
      "100.00",
    );
    expect(await (await ass.request.get(path + "/report")).text()).toBe(report);
  } finally {
    await association.close();
    await operator.close();
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
      { cwd: "../server" },
    );
    cleanup(code);
  }
});

test("legacy draft status stays unknown until explicitly chosen and weight preview never advances its edit version", async ({
  page,
}) => {
  const code = "browser-config-" + randomBytes(8).toString("hex");
  try {
    await login(page, "association");
    const csrf = await token(page);
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    const created = await page.request.post("/api/v1/ratings/standard-drafts", {
      headers: { "x-csrf-token": csrf },
      data: {
        data: {
          code,
          name: "旧草稿状态确认",
          version: "1",
          categories: [{ code: "general", name: "综合", weight: "100" }],
          indicators: [
            {
              code: "one",
              name: "单项",
              category: "general",
              source: "manual",
              weight: "1",
              max_score: "100",
              description: "旧草稿未留状态",
              field_name: "expert_score",
              effective_from: today,
              bands: [{ lower: "0", upper: null, score: "100", label: "档位" }],
            },
          ],
          thresholds: [{ minimum: "60", stars: "1" }],
        },
      },
    });
    expect(created.status()).toBe(201);
    const draft = await created.json();
    expect(draft.data.indicators[0].enabled).toBeNull();
    await page.getByRole("button", { name: "星级评价", exact: true }).click();
    await page
      .getByRole("button", { name: "配置新标准版本", exact: true })
      .click();
    const work = page.getByRole("region", {
      name: "标准草稿工作区",
      exact: true,
    });
    await work
      .getByRole("button", { name: `载入标准草稿 #${draft.id}`, exact: true })
      .click();
    const form = work.locator("form.standard-builder");
    await expect(form.getByLabel("指标1状态", { exact: true })).toHaveValue("");
    const premature = await page.request.post(
      `/api/v1/ratings/standard-drafts/${draft.id}/finalize`,
      { headers: { "x-csrf-token": csrf }, data: { version: 1 } },
    );
    expect(premature.status()).toBe(422);
    await form
      .getByLabel("指标1状态", { exact: true })
      .selectOption("disabled");
    await form
      .getByRole("button", { name: "预览参与指标与权重", exact: true })
      .click();
    await expect(form.getByRole("alert")).toContainText("至少一个启用指标");
    await form.getByLabel("指标1状态", { exact: true }).selectOption("enabled");
    const saved = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/standard-drafts/${draft.id}`) &&
        r.request().method() === "PATCH",
    );
    await form
      .getByRole("button", { name: "暂存标准草稿", exact: true })
      .click();
    expect((await (await saved).json()).version).toBe(2);
    await form
      .getByRole("button", { name: "预览参与指标与权重", exact: true })
      .click();
    await expect(
      form.getByRole("region", { name: "指标参与权重预览", exact: true }),
    ).toBeVisible();
    const finalized = page.waitForResponse((r) =>
      r.url().endsWith(`/standard-drafts/${draft.id}/finalize`),
    );
    await form
      .getByRole("button", { name: "保存标准版本", exact: true })
      .click();
    const result = await (await finalized).json();
    expect(result.draft.version).toBe(3);
    expect(result.standard.rules.indicators[0].enabled).toBe(true);
  } finally {
    cleanup(code);
  }
});
