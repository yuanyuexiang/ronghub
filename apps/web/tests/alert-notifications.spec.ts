import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(module: string, args: string[]) {
  return execFileSync("uv", ["run", "python", "-m", module, ...args], {
    cwd: "../server",
    encoding: "utf8",
  });
}
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await navigateWorkspace(page, user === "regulator" ? "告警数据" : "企业告警");
  if (user === "regulator")
    await page
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "通知设置", exact: true })
      .click();
}
test("private notification channels and recipients freeze masked simulation history without granting identity", async ({
  browser,
}) => {
  const marker = "browser-notify-" + randomBytes(8).toString("hex"),
    code = "browser-alertcfg-" + randomBytes(8).toString("hex"),
    snapshot = JSON.parse(
      fixture("app.alert_notification_fixture", ["snapshot"]),
    );
  let version = snapshot.version;
  const owned = new Map<number, number>();
  const station = JSON.parse(
    fixture("app.browser_fixture", ["create", "--operating", "--located"]),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const manager = await contexts[0].newPage(),
      owner = await contexts[1].newPage();
    await login(manager, "regulator");
    const panel = manager.getByRole("region", {
      name: "告警通知配置",
      exact: true,
    });
    await panel
      .getByRole("button", { name: "管理通知与接收人", exact: true })
      .click();
    await panel
      .getByRole("button", { name: "配置通知 设备充电告警", exact: true })
      .click();
    const pref = panel.getByRole("form", { name: "通知类型表单", exact: true });
    await pref
      .getByLabel("类型站内通知开关", { exact: true })
      .selectOption("off");
    await pref.getByLabel("类型短信通知开关", { exact: true }).check();
    await manager
      .getByRole("button", { name: "刷新数据", exact: true })
      .click();
    await expect(
      pref.getByLabel("类型站内通知开关", { exact: true }),
    ).toHaveValue("off");
    let preferenceSaved = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/notifications/preferences/device_charging") &&
        r.request().method() === "PUT",
    );
    await pref
      .getByRole("button", { name: "保存通知类型", exact: true })
      .click();
    const firstPreference = await preferenceSaved;
    expect(firstPreference.status()).toBe(200);
    version = (await firstPreference.json()).version;
    await panel
      .getByRole("button", { name: "新增告警接收人", exact: true })
      .click();
    const edit = panel.getByRole("form", {
      name: "告警接收人表单",
      exact: true,
    });
    const phone =
        "188" +
        String(randomBytes(4).readUInt32BE(0) % 100000000).padStart(8, "0"),
      secondPhone =
        "189" +
        String(randomBytes(4).readUInt32BE(0) % 100000000).padStart(8, "0");
    await edit.getByLabel("告警接收人姓名", { exact: true }).fill(marker);
    await edit.getByLabel("告警接收人手机号", { exact: true }).fill(phone);
    await edit.getByLabel("接收类型 设备充电告警", { exact: true }).check();
    await manager
      .getByRole("button", { name: "刷新数据", exact: true })
      .click();
    await expect(
      edit.getByLabel("告警接收人手机号", { exact: true }),
    ).toHaveValue(phone);
    const created = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/notifications/recipients") &&
        r.request().method() === "POST",
    );
    await edit
      .getByRole("button", { name: "保存告警接收人", exact: true })
      .click();
    const contactResponse = await created;
    expect(contactResponse.status(), await contactResponse.text()).toBe(201);
    const contact = await contactResponse.json();
    owned.set(contact.id, contact.version);
    await panel.getByLabel("告警接收人检索", { exact: true }).fill(marker);
    await panel
      .getByRole("button", { name: "查询告警接收人", exact: true })
      .click();
    await expect(panel.locator(".alert-recipient-table")).toContainText(
      contact.masked_phone,
    );
    const me = await (await manager.request.get("/api/v1/auth/me")).json();
    const headers = { "x-csrf-token": me.csrf_token };
    const ruleResponse = await manager.request.post(
      "/api/v1/alerts/configured-rules",
      {
        headers,
        data: {
          code,
          version: 1,
          name: marker,
          configuration: {
            configuration_version: 1,
            kind: "device_temperature",
            direction: "gte",
            period: "instant",
            thresholds: { general: "60", critical: "80" },
            recover_value: "50",
          },
        },
      },
    );
    expect(ruleResponse.status()).toBe(201);
    const rule = await ruleResponse.json();
    const equipment = await (
      await manager.request.get(
        `/api/v1/alerts/configured-observations/equipment?station_id=${station.station_id}`,
      )
    ).json();
    const sample = {
      request_key: randomUUID(),
      station_id: station.station_id,
      equipment_id: equipment.items[0].id,
      rule_id: rule.id,
      observed_at: "2026-11-01T08:00:00+08:00",
      value: "80",
      coverage_complete: true,
    };
    const first = await manager.request.post(
      "/api/v1/alerts/configured-observations",
      { headers, data: sample },
    );
    expect(first.status()).toBe(201);
    const firstResult = await first.json();
    const eventId = firstResult.event_id;
    expect(
      (await (await manager.request.get(`/api/v1/alerts/${eventId}`)).json())
        .notices,
    ).toHaveLength(0);
    await panel
      .getByRole("button", { name: "查看通知记录", exact: true })
      .click();
    await panel
      .getByLabel("通知记录事件编号", { exact: true })
      .fill(String(eventId));
    await panel
      .getByRole("button", { name: "查询通知记录", exact: true })
      .click();
    await expect(panel.locator(".alert-sms-table")).toContainText(
      "模拟回执（未实际发送）",
    );
    await expect(panel.locator(".alert-sms-table")).toContainText(
      contact.masked_phone,
    );
    const firstDelivery = await manager.request.get(
      `/api/v1/alerts/notifications/deliveries?event_id=${eventId}`,
    );
    expect(await firstDelivery.text()).not.toContain(phone);
    await panel
      .getByRole("button", { name: `修改接收人 ${marker}`, exact: true })
      .click();
    await edit
      .getByLabel("告警接收人姓名", { exact: true })
      .fill(marker + "-edited");
    await edit
      .getByLabel("告警接收人手机号", { exact: true })
      .fill(secondPhone);
    const modified = manager.waitForResponse(
      (r) =>
        r.url().endsWith(`/notifications/recipients/${contact.id}`) &&
        r.request().method() === "PATCH",
    );
    await edit
      .getByRole("button", { name: "保存告警接收人", exact: true })
      .click();
    const modifiedResponse = await modified;
    expect(modifiedResponse.status()).toBe(200);
    const updated = await modifiedResponse.json();
    owned.set(updated.id, updated.version);
    const recovered = await manager.request.post(
      "/api/v1/alerts/configured-observations",
      {
        headers,
        data: {
          ...sample,
          request_key: randomUUID(),
          observed_at: "2026-11-01T08:01:00+08:00",
          value: "50",
        },
      },
    );
    expect(recovered.status()).toBe(201);
    await panel
      .getByRole("button", { name: "查询通知记录", exact: true })
      .click();
    await expect(panel.locator(".alert-sms-table")).toContainText(
      updated.masked_phone,
    );
    await expect(panel.locator(".alert-sms-table")).toContainText(
      contact.masked_phone,
    );
    manager.once("dialog", (dialog) => dialog.accept());
    const deleting = manager.waitForResponse(
      (r) =>
        r
          .url()
          .endsWith(
            `/notifications/recipients/${contact.id}?version=${updated.version}`,
          ) && r.request().method() === "DELETE",
    );
    await panel
      .getByRole("button", { name: `删除接收人 ${marker}-edited`, exact: true })
      .click();
    const deleted = await deleting;
    expect(deleted.status()).toBe(200);
    owned.set(contact.id, (await deleted.json()).version);
    await expect(panel.locator(".alert-recipient-table")).not.toContainText(
      marker,
    );
    await panel
      .getByRole("button", { name: "配置通知 设备充电告警", exact: true })
      .click();
    await pref
      .getByLabel("类型站内通知开关", { exact: true })
      .selectOption("on");
    await pref.getByLabel("类型短信通知开关", { exact: true }).uncheck();
    preferenceSaved = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/notifications/preferences/device_charging") &&
        r.request().method() === "PUT",
    );
    await pref
      .getByRole("button", { name: "保存通知类型", exact: true })
      .click();
    const nextPreference = await preferenceSaved;
    expect(nextPreference.status()).toBe(200);
    version = (await nextPreference.json()).version;
    const next = await manager.request.post(
      "/api/v1/alerts/configured-observations",
      {
        headers,
        data: {
          ...sample,
          request_key: randomUUID(),
          observed_at: "2026-11-01T08:02:00+08:00",
        },
      },
    );
    expect(next.status()).toBe(201);
    const nextResult = await next.json();
    const nextEvent = await (
      await manager.request.get(`/api/v1/alerts/${nextResult.event_id}`)
    ).json();
    expect(nextEvent.notices).toHaveLength(1);
    const oldHistory = await (
      await manager.request.get(
        `/api/v1/alerts/notifications/deliveries?event_id=${eventId}`,
      )
    ).json();
    expect(oldHistory.total).toBe(2);
    expect(
      oldHistory.items.map((row: { masked_phone: string }) => row.masked_phone),
    ).toEqual(
      expect.arrayContaining([contact.masked_phone, updated.masked_phone]),
    );
    await panel.screenshot({
      path: "../../.backups/alert-notifications-browser.png",
    });
    await login(owner, "operator_a");
    await expect(
      owner.getByRole("region", { name: "告警通知配置", exact: true }),
    ).toHaveCount(0);
    for (const suffix of [
      "preferences",
      "recipients",
      "routings",
      "deliveries",
    ])
      expect(
        (
          await owner.request.get("/api/v1/alerts/notifications/" + suffix)
        ).status(),
      ).toBe(403);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    fixture("app.browser_fixture", [
      "clean",
      "--station",
      String(station.station_id),
    ]);
    fixture("app.alert_configuration_fixture", ["--code", code]);
    fixture("app.alert_notification_fixture", [
      "restore",
      "--token",
      snapshot.token,
      "--version",
      String(version),
      "--marker",
      marker,
      ...Array.from(owned).flatMap(([id, v]) => ["--recipient", `${id}:${v}`]),
    ]);
  }
});
