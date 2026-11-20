import { expect, type Page } from "./fixtures";

/** Follow the explicit return path introduced by prototype correction. */
export async function returnToBusinessList(page: Page, module: string) {
  const close: Record<string, string> = {
    补贴: "关闭补贴详情",
    星级: "关闭评价详情",
    巡检: "关闭任务详情",
    告警: "关闭告警详情",
    督办: "返回督办列表",
  };
  const button = page.getByRole("button", { name: close[module], exact: true });
  if (await button.isVisible()) await button.click();
  await expect(button).not.toBeVisible();
  const views: Record<string, [string, string]> = {
    补贴: ["补贴业务视图", "补贴申请记录"],
    星级: ["星级评价业务视图", "企业申报查看"],
    巡检: ["巡检业务视图", "巡检任务管理"],
  };
  if (views[module]) {
    const [label, name] = views[module];
    const nav = page.getByRole("navigation", { name: label, exact: true });
    await expect(nav).toBeVisible();
    const item = nav.getByRole("button", { name: name, exact: true });
    if (await item.isVisible()) await item.click();
    else if (module === "星级")
      await nav
        .getByRole("button", { name: "企业申报管理", exact: true })
        .click();
    else if (module === "巡检")
      await nav
        .getByRole("button", { name: "企业整改任务", exact: true })
        .click();
  }
  await expect(button).not.toBeVisible();
}
