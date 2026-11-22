import {
  test as base,
  expect,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { execFileSync } from "node:child_process";
export * from "@playwright/test";
// Browser scenarios share a localhost gateway. Security rate limits have separate
// PG tests with isolated IPs; do not let unrelated scenarios share throttle state.
export async function prepareCaptcha(page: Page) {
  const image = page.getByRole("img", { name: "登录图片验证码", exact: true });
  await expect(image).toHaveAttribute("data-captcha-id", /.+/);
  const token = (await image.getAttribute("data-captcha-id"))!;
  if ((await image.getAttribute("data-fixture-captcha-id")) === token) {
    await page.getByLabel("图片验证码", { exact: true }).fill("ABCDE");
    return;
  }
  const seeded = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.captcha_fixture", "--", token],
      {
        cwd: "../server",
        encoding: "utf8",
      },
    ),
  );
  await image.evaluate(
    (node, values) => {
      (node as HTMLImageElement).src = values.image;
      node.setAttribute("data-fixture-captcha-id", values.token);
    },
    { image: seeded.image, token },
  );
  await page.getByLabel("图片验证码", { exact: true }).fill(seeded.answer);
}
export async function openWorkspaceNavigation(page: Page) {
  const toggle = page.getByRole("button", {
    name: "展开或收起功能导航",
    exact: true,
  });
  if (
    (await toggle.isVisible()) &&
    (await toggle.getAttribute("aria-expanded")) === "false"
  ) {
    await toggle.click();
  }
}

export const test = base.extend<{
  localAuthState: void;
  automaticCaptcha: boolean;
}>({
  automaticCaptcha: [true, { option: true }],
  localAuthState: [
    async ({ browser, automaticCaptcha }, use) => {
      execFileSync(
        "uv",
        ["run", "python", "-m", "app.browser_fixture", "prepare"],
        {
          cwd: "../server",
          stdio: "pipe",
        },
      );
      const original = browser.newContext.bind(browser);
      function install(context: BrowserContext) {
        context.on("page", (page) => {
          if (automaticCaptcha)
            void page.addLocatorHandler(
              page.locator('input[aria-label="图片验证码"][value=""]'),
              () => prepareCaptcha(page),
              { noWaitAfter: true },
            );
        });
      }
      browser.newContext = async (...args) => {
        const context = await original(...args);
        install(context);
        return context;
      };
      try {
        await use();
      } finally {
        browser.newContext = original;
      }
    },
    { auto: true },
  ],
});

/** Open the actual canvas menu (desktop flyout/mobile tree), or an auxiliary/page action. */
export async function navigateWorkspace(
  page: Page,
  name: string | RegExp,
  allowParent = true,
) {
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  const aliases: Record<string, string[]> = {
    场站地图: ["可视化一张图"],
    全域地图: ["可视化一张图"],
    场站台账: ["场站数据同步与场站信息查询", "信息推送", "多维度数据筛选"],
    场站信息查询: ["场站数据同步与场站信息查询"],
    星级查看: ["企业申报查看"],
    星级评价: ["企业申报管理"],
    星级申报: ["评定进度查询"],
    新增星级申报: ["申报材料填报"],
    企业申报查看: ["企业申报管理", "评定进度查询"],
    统计分析: ["充电量统计"],
    补贴申报: ["补贴申请管理"],
    补贴申请记录: ["补贴申请管理"],
    政策与单站申报: ["新建补贴申请"],
    规则配置: ["告警配置"],
    事件跟踪: ["告警数据"],
    补贴管理: ["补贴申请记录"],
    巡检督办: ["巡检任务管理"],
    整改任务: ["巡检任务"],
    场站申请: ["接入场站管理"],
    公告与通知: ["新增发布资讯"],
    评价结果查看: ["评审结果查看"],
    评价结果与公示: ["公示查询查看", "公示查询服务"],
    公示查询查看: ["公示查询服务"],
    数据支撑查看: ["数据支撑评价"],
    评审过程查看: ["评审过程管理"],
    场站数据同步与场站信息查询: ["信息推送", "多维度数据筛选"],
    观测电量分析: ["充电量统计", "统计分析"],
    充电与补贴报表: ["运营商充电及补贴统计分析", "充电与补贴数据统计"],
    系统操作日志: ["操作日志"],
    监管协议: ["监管协议开发"],
    充电时段: ["充电时段统计"],
    巡检计划创建: ["巡检计划"],
  };
  const names: (string | RegExp)[] = [
    name,
    ...(typeof name === "string"
      ? (aliases[name] ?? [])
      : Object.entries(aliases)
          .filter(([key]) => name.test(key))
          .flatMap(([, values]) => values)),
  ];
  const nav = page.getByRole("navigation", {
    name: "业务模块导航",
    exact: true,
    includeHidden: true,
  });
  const extra = page.getByRole("navigation", {
    name: "辅助功能导航",
    exact: true,
    includeHidden: true,
  });
  // Exact auxiliary names retain their original module semantics (e.g. operator energy analysis).
  for (const candidate of names) {
    const local = page.locator("main.content").getByRole("button", {
      name: candidate,
      exact: true,
      includeHidden: true,
    });
    for (const detail of await local
      .first()
      .locator("xpath=ancestor::details")
      .all()) {
      if (
        (await detail.getAttribute("open")) === null &&
        (await detail.isVisible())
      )
        await detail.locator(":scope > summary").click();
    }
    if ((await local.count()) && (await local.first().isVisible())) {
      await local.first().click();
      return;
    }
  }
  for (const candidate of names) {
    const target = nav.getByRole("button", {
      name: candidate,
      exact: true,
      includeHidden: true,
    });
    if (await target.count()) {
      await openWorkspaceNavigation(page);
      const button = target.first();
      const group = button.locator(
        'xpath=ancestor::section[contains(@class,"navigation-group")]',
      );
      const trigger = group.locator(":scope > button[aria-expanded]");
      if (
        (await trigger.count()) &&
        (await trigger.getAttribute("aria-expanded")) === "false"
      )
        await trigger.click();
      for (const entry of await button
        .locator('xpath=ancestor::div[contains(@class,"navigation-entry")]')
        .all()) {
        const parent = entry.locator(":scope > button[aria-expanded]");
        if (
          (await parent.count()) &&
          (await parent.getAttribute("aria-expanded")) === "false"
        )
          await parent.click();
      }
      await button.click();
      return;
    }
    const auxiliary = extra.getByRole("button", {
      name: candidate,
      exact: true,
      includeHidden: true,
    });
    if (await auxiliary.count()) {
      const more = page.getByRole("button", { name: "更多功能", exact: true });
      if ((await more.getAttribute("aria-expanded")) === "false")
        await more.click();
      await auxiliary.first().click();
      return;
    }
  }
  for (const candidate of names) {
    const local = page.getByRole("button", { name: candidate, exact: true });
    if (await local.count()) {
      await local.first().click();
      return;
    }
  }
  const parentByAction: Record<string, string> = {
    评价标准查看: "评定进度查询",
    评价结果与公示: "评定进度查询",
    评审过程管理: "评定进度查询",
    数据支撑评价: "评定进度查询",
    年度多站申报: "补贴申请管理",
    政策与单站申报: "补贴申请管理",
    补贴统计: "补贴申请管理",
    设备电量数据: "补贴申请管理",
    电量日汇总: "补贴申请管理",
  };
  const parent = typeof name === "string" ? parentByAction[name] : undefined;
  if (allowParent && parent) {
    await navigateWorkspace(page, parent, false);
    await navigateWorkspace(page, name, false);
    return;
  }
  await page.getByRole("button", { name, exact: true }).click();
}
