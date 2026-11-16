"""Module capabilities narrow coarse business roles; delegation cannot add authority."""

from fastapi import HTTPException

from app.models import RoleProfile

LABELS = {
    "audits.read": "操作日志检索",
    "registrations.read": "企业注册查看",
    "registrations.review": "企业注册审核",
    "stations.read": "场站查看",
    "applications.read": "场站申请查看",
    "applications.create": "场站申请建草稿",
    "applications.edit": "场站申请编辑",
    "applications.materials": "场站申请材料维护",
    "applications.submit": "场站申请提交",
    "applications.withdraw": "场站申请撤回",
    "applications.review": "场站申请审核",
    "sync.read": "数据同步查看",
    "sync.write": "发起数据同步",
    "subsidy.read": "补贴查看",
    "subsidy.export": "补贴申报与办理过程导出",
    "subsidy.create": "补贴核算草稿",
    "subsidy.edit": "补贴草稿说明维护",
    "subsidy.materials": "补贴申报ZIP材料维护",
    "subsidy.submit": "补贴提交审核",
    "subsidy.withdraw": "补贴撤回",
    "subsidy.adjust": "补贴金额调整",
    "subsidy.review": "补贴审核通过/驳回",
    "subsidy.disburse": "补贴发放登记",
    "subsidy.policies": "补贴政策版本管理",
    "ratings.read": "评价查看",
    "ratings.export": "星级申报与评审过程导出",
    "ratings.create": "评星建草稿/复评申诉",
    "ratings.edit": "评星申报编辑",
    "ratings.materials": "评星材料维护",
    "ratings.submit": "评星提交",
    "ratings.withdraw": "评星撤回",
    "ratings.review": "评星复核/驳回",
    "ratings.publish": "评星公示",
    "ratings.delist": "评星摘牌",
    "ratings.standards": "评价标准管理",
    "inspections.read": "巡检查看",
    "inspections.assign": "巡检任务下发",
    "inspections.materials": "巡检整改材料维护",
    "inspections.reply": "巡检整改回复",
    "inspections.return": "巡检退回补充",
    "inspections.close": "巡检复核关闭",
    "alerts.read": "告警查看",
    "alerts.rules": "告警规则版本配置",
    "alerts.samples": "告警演示样本验证",
    "alerts.acknowledge": "告警人工确认",
    "content.read": "公告通知查看",
    "content.manage": "内容草稿与历史查看",
    "content.create": "内容草稿创建",
    "content.edit": "内容草稿编辑",
    "content.publish": "内容发布/定时发布",
    "content.withdraw": "内容撤下",
    "content.reply": "回复通知",
    "content.delete": "内容草稿删除",
    "content.export": "内容与通知元数据导出",
    "companies.read": "企业档案查看",
    "companies.write": "企业资料维护",
    "companies.status": "企业启停",
    "companies.export": "企业导出",
    "orders.read": "源报订单查看",
    "orders.export": "源报订单导出",
    "statistics.read": "统计查看",
    "statistics.export": "统计数据导出",
    "accounts.manage": "账号管理",
    "roles.manage": "权限角色管理",
}
BASE = {
    "regulator": set(LABELS),
    "association": {
        "stations.read",
        "ratings.read",
        "ratings.export",
        "ratings.review",
        "ratings.publish",
        "ratings.delist",
        "ratings.standards",
        "content.read",
        "statistics.read",
    },
    "operator": {
        "stations.read",
        "applications.read",
        "applications.create",
        "applications.edit",
        "applications.materials",
        "applications.submit",
        "applications.withdraw",
        "subsidy.read",
        "subsidy.export",
        "subsidy.create",
        "subsidy.edit",
        "subsidy.materials",
        "subsidy.submit",
        "subsidy.withdraw",
        "ratings.read",
        "ratings.export",
        "ratings.create",
        "ratings.edit",
        "ratings.materials",
        "ratings.submit",
        "ratings.withdraw",
        "inspections.read",
        "inspections.materials",
        "inspections.reply",
        "alerts.read",
        "content.read",
        "content.reply",
        "companies.read",
        "companies.write",
        "companies.export",
        "statistics.read",
        "statistics.export",
        "orders.read",
        "orders.export",
    },
}


SUBSIDY_ACTIONS = {
    "submit": "subsidy.submit",
    "withdraw": "subsidy.withdraw",
    "adjust": "subsidy.adjust",
    "approve": "subsidy.review",
    "reject": "subsidy.review",
    "record_disbursement": "subsidy.disburse",
}


APPLICATION_ACTIONS = {
    "submit": "applications.submit",
    "withdraw": "applications.withdraw",
    "approve": "applications.review",
    "reject": "applications.review",
}


RATING_ACTIONS = {
    "submit": "ratings.submit",
    "withdraw": "ratings.withdraw",
    "review": "ratings.review",
    "reject": "ratings.review",
    "publish": "ratings.publish",
    "delist": "ratings.delist",
}


CONTENT_ACTIONS = {
    "publish": "content.publish",
    "withdraw": "content.withdraw",
    "delete": "content.delete",
}

INSPECTION_ACTIONS = {
    "reply": "inspections.reply",
    "return": "inspections.return",
    "close": "inspections.close",
}


def normalize_grants(role, grants):
    result = set(grants)
    for module in ["subsidy", "applications", "ratings", "inspections", "alerts", "content"]:
        legacy = module + ".write"
        if legacy in result:
            if role not in (
                {"operator", "regulator", "association"}
                if module == "ratings"
                else {"operator", "regulator"}
            ):
                raise ValueError("当前基础身份无此模块办理权限")
            if module in {"alerts", "content"} and role != "regulator":
                raise ValueError("仅监管身份可办理此模块")
            result.remove(legacy)
            result.update(
                cap
                for cap in BASE.get(role, set())
                if cap.startswith(module + ".")
                and cap not in {module + ".read", module + ".export"}
                and not (module == "subsidy" and cap in {"subsidy.edit", "subsidy.materials"})
                and not (
                    module == "content"
                    and cap in {"content.reply", "content.delete", "content.export"}
                )
            )
    return sorted(result)


def permissions(db, user):
    base = BASE.get(user.role, set())
    if user.role_profile_id is None:
        return set(base)
    profile = db.get(RoleProfile, user.role_profile_id)
    if not profile or profile.base_role != user.role:
        return set()
    return base.intersection(profile.permissions)


def require(db, user, capability):
    if capability not in permissions(db, user):
        raise HTTPException(403, "当前账号未获该业务权限")


def authorize_business_write(db, user, request, capability):
    from sqlalchemy import text

    from app.password_management import session_active

    # Grants use the exclusive variant of this same transaction lock. Concurrent
    # independent claims can proceed, but revoked permissions cannot pass stale cache.
    db.execute(text("SELECT pg_advisory_xact_lock_shared(78230012)"))
    db.expire_all()
    db.refresh(user)
    session_active(db, request, user)
    if user.must_change_password:
        raise HTTPException(403, "请先修改临时密码")
    require(db, user, capability.split(".")[0] + ".read")
    require(db, user, capability)


def management_requested(request):
    # Match the true spellings accepted by FastAPI/Pydantic for bool query fields.
    return request.query_params.get("manage", "").lower() in {"true", "1", "on", "yes"}


def request_capability(request):
    path = request.url.path.removeprefix("/api/v1/").strip("/")
    if path in {
        "auth/me",
        "auth/logout",
        "auth/password",
        "auth/phone",
        "auth/phone/challenges",
        "auth/phone/confirm",
    }:
        return None
    parts = path.split("/")
    head = parts[0]
    read = request.method in {"GET", "HEAD", "OPTIONS"}
    if head in {"stations", "operators", "dashboard", "map"}:
        return "stations.read"
    if head == "attachments":
        return "applications.read" if read else "applications.materials"
    if head == "applications":
        if read:
            return "applications.read"
        if request.method == "POST" and parts[-1] == "attachments":
            return "applications.materials"
        if request.method == "POST" and len(parts) == 1:
            return "applications.create"
        if request.method == "PATCH" and len(parts) == 2:
            return "applications.edit"
        if request.method == "POST" and len(parts) == 3 and parts[-1] == "actions":
            return "applications.read"
        raise HTTPException(403, "场站申请接口未配置动作权限")
    if head == "orders":
        if not read:
            raise HTTPException(403, "订单仅提供授权只读查询")
        return "orders.export" if parts[-1] == "export" else "orders.read"
    if head == "statistics":
        return "statistics.export" if parts[-1] == "export" else "statistics.read"
    if head == "companies":
        if parts[-1] == "export":
            return "companies.export"
        if parts[-1] == "status":
            return "companies.status"
        return "companies.read" if read else "companies.write"
    if head == "content":
        if read and parts[-1] == "export":
            return "content.export"
        if not read and parts[-1] == "batch-actions":
            return "content.manage"
        if read and parts[-1] == "operator-options":
            return "content.manage"
        if not read and "attachments" in parts:
            return "content.edit"
        if not read and len(parts) >= 3 and parts[2] == "replies":
            return "content.reply"
        if read:
            return "content.manage" if management_requested(request) else "content.read"
        if request.method == "POST" and len(parts) == 1:
            return "content.create"
        if request.method == "PATCH" and len(parts) == 2:
            return "content.edit"
        if request.method == "POST" and len(parts) == 3 and parts[-1] == "actions":
            return "content.manage"
        raise HTTPException(403, "内容接口未配置动作权限")
    if head == "registrations":
        return "registrations.read" if read else "registrations.review"
    if head == "audits":
        return "audits.read"
    if head == "staff-applications":
        return "accounts.manage"
    if head == "accounts":
        return "accounts.manage"
    if head == "roles":
        return "roles.manage"
    if head == "ratings":
        if len(parts) >= 2 and parts[1] == "standard-drafts":
            return "ratings.standards"
        if len(parts) >= 3 and parts[1] == "standards" and not read:
            return "ratings.standards"
        if read:
            return "ratings.export" if parts[-1] == "export" else "ratings.read"
        if "attachments" in parts:
            return "ratings.materials"
        if request.method == "POST" and len(parts) == 2 and parts[1] == "standards":
            return "ratings.standards"
        if request.method == "POST" and len(parts) == 2 and parts[1] == "applications":
            return "ratings.create"
        if request.method == "PATCH" and len(parts) == 3 and parts[1] == "applications":
            return "ratings.edit"
        if request.method == "POST" and len(parts) == 4 and parts[-1] == "actions":
            return "ratings.read"
        raise HTTPException(403, "评价接口未配置动作权限")
    if head == "subsidy":
        if request.method == "POST" and parts == ["subsidy", "annual-batches"]:
            return "subsidy.create"
        if request.method == "PATCH" and len(parts) == 3 and parts[1] == "claims":
            return "subsidy.edit"
        if (
            (
                (request.method == "POST" and len(parts) == 4)
                or (request.method == "DELETE" and len(parts) == 5)
            )
            and parts[1] == "claims"
            and parts[3] == "attachments"
        ):
            return "subsidy.materials"
        if request.method == "POST" and parts == ["subsidy", "claims", "batch-actions"]:
            return "subsidy.review"
        if read:
            return "subsidy.export" if parts[-1] == "export" else "subsidy.read"
        if len(parts) == 2 and parts[1] == "policies" and request.method == "POST":
            return "subsidy.policies"
        if len(parts) == 2 and parts[1] == "claims" and request.method == "POST":
            return "subsidy.create"
        if (
            len(parts) == 4
            and parts[1] == "claims"
            and parts[3] == "actions"
            and request.method == "POST"
        ):
            # The route checks the parsed action before any mutation.
            return "subsidy.read"
        raise HTTPException(403, "该补贴接口尚未配置动作权限")
    if head == "inspections":
        if read:
            return "inspections.read"
        if "attachments" in parts:
            return "inspections.materials"
        if request.method == "POST" and len(parts) == 1:
            return "inspections.assign"
        if request.method == "POST" and len(parts) == 3 and parts[-1] == "actions":
            return "inspections.read"
        raise HTTPException(403, "巡检接口未配置动作权限")
    if head == "alerts":
        if read:
            return "alerts.read"
        if request.method == "POST" and parts == ["alerts", "rules"]:
            return "alerts.rules"
        if request.method == "POST" and parts == ["alerts", "samples"]:
            return "alerts.samples"
        if request.method == "POST" and len(parts) == 3 and parts[-1] == "acknowledge":
            return "alerts.acknowledge"
        raise HTTPException(403, "告警接口未配置动作权限")
    if head == "sync":
        return head + (".read" if read else ".write")
    # New endpoints require an explicit capability mapping before becoming accessible.
    raise HTTPException(403, "该接口尚未配置授权规则")
