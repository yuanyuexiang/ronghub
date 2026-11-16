"""Versioned example rating formula. Submitted scores are declarations, not verified telemetry."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select, update

from app import rating_configuration, rating_results
from app.business_audit import state, transition
from app.config import settings
from app.models import (
    RatingAction,
    RatingApplication,
    RatingAttachment,
    RatingStandard,
    Station,
    now,
)
from app.scopes import records as scoped_records
from app.scopes import station_scope


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "评价规则未确认，示例评定仅限演示环境")


def calculate(rules, scores):
    indicators = rules["indicators"]
    if set(scores) - {i["code"] for i in indicators}:
        raise HTTPException(422, "评分包含未知指标")
    plan = rating_configuration.weight_plan(rules)
    active = rating_configuration.active_indicators(rules)
    if plan and any(scores.get(code) is not None for code in plan["excluded"]):
        raise HTTPException(422, "停用指标不接收评分；不记缺失，也不以零分参与")
    participation = (
        {
            item["code"]: {
                "enabled": item["enabled"],
                "normalization_weight": item["normalization_weight"],
                "effective_weight_percent": item["effective_weight_percent"],
            }
            for item in plan["items"]
        }
        if plan
        else {}
    )
    scores, indicator_evidence = rating_configuration.mapped_values(rules, scores)
    categories = rules.get("categories") or [{"code": "general", "name": "综合", "weight": "1"}]
    categorized = bool(rules.get("categories"))
    total_weight = sum((Decimal(c["weight"]) for c in categories), Decimal(0))
    if total_weight <= 0:
        raise HTTPException(422, "分类权重总和必须大于零")
    weighted_total = Decimal(0)
    details = []
    groups = []
    missing = []
    for category in categories:
        members = [i for i in active if not categorized or i.get("category") == category["code"]]
        weight_sum = sum((Decimal(i["weight"]) for i in members), Decimal(0))
        if not members or weight_sum <= 0:
            raise HTTPException(422, "分类必须含有指标且权重总和大于零")
        subtotal = Decimal(0)
        group_missing = []
        for i in members:
            raw = scores.get(i["code"])
            if raw is None:
                missing.append(i["code"])
                group_missing.append(i["code"])
                details.append(
                    {
                        **indicator_evidence.get(i["code"], {}),
                        **participation.get(i["code"], {}),
                        "code": i["code"],
                        "name": i["name"],
                        "category": category["code"],
                        "score": None,
                        "source": i["source"],
                    }
                )
                continue
            try:
                score = Decimal(str(raw))
            except (InvalidOperation, ValueError) as e:
                raise HTTPException(422, "评分必须是有效数值") from e
            maximum, weight = Decimal(i["max_score"]), Decimal(i["weight"])
            if not score.is_finite() or score < 0 or score > maximum:
                raise HTTPException(422, "评分超出指标范围")
            contribution = score / maximum * Decimal(100) * weight / weight_sum
            subtotal += contribution
            details.append(
                {
                    **indicator_evidence.get(i["code"], {}),
                    **participation.get(i["code"], {}),
                    "code": i["code"],
                    "name": i["name"],
                    "category": category["code"],
                    "score": str(score),
                    "max_score": str(maximum),
                    "weight": str(weight),
                    "weighted_score": str(
                        contribution * Decimal(category["weight"]) / total_weight
                    ),
                    "source": i["source"],
                }
            )
        groups.append(
            {
                "code": category["code"],
                "name": category["name"],
                "weight": str(category["weight"]),
                "score": None
                if group_missing
                else str(subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "missing": group_missing,
                **({"indicator_weight_sum": str(weight_sum)} if plan else {}),
            }
        )
        weighted_total += subtotal * Decimal(category["weight"]) / total_weight
    if plan:
        for item in indicators:
            if not item["enabled"]:
                details.append(
                    {
                        "code": item["code"],
                        "name": item["name"],
                        "category": item.get("category", "general") if categorized else "general",
                        "source": item["source"],
                        "field_name": item.get("field_name"),
                        "effective_from": item.get("effective_from"),
                        "score": None,
                        "weight": str(item["weight"]),
                        "max_score": str(item["max_score"]),
                        "exclusion_reason": "版本内停用，不参与评分或缺失判断",
                        **participation[item["code"]],
                    }
                )
    total = None if missing else weighted_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    stars = None
    if total is not None:
        stars = 0
        for threshold in sorted(
            rules["thresholds"], key=lambda t: Decimal(t["minimum"]), reverse=True
        ):
            if total >= Decimal(threshold["minimum"]):
                stars = threshold["stars"]
                break
    return {
        "total": str(total) if total is not None else None,
        "stars": stars,
        "missing": missing,
        "excluded": plan["excluded"] if plan else None,
        "details": details,
        "categories": groups,
        "formula": "category_weighted" if categorized else "legacy_flat",
        "basis": "示例分类/指标两级加权初算；自评和人工复核不代表已验证运行数据",
    }


def create(
    db,
    user,
    station_id,
    standard_id,
    scores,
    note,
    kind="initial",
    parent_id=None,
    parent_version=None,
):
    demo_only()
    if user.role != "operator" or user.operator_id is None:
        raise HTTPException(403, "仅运营商可申报星级")
    station = db.scalar(
        station_scope(select(Station), user)
        .where(Station.id == station_id, Station.operator_id == user.operator_id)
        .with_for_update()
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    if station.status not in {"access_approved", "accepted", "operating"}:
        raise HTTPException(409, "仅已接入通过且未核销的场站可申报")
    standard = db.scalar(
        select(RatingStandard).where(RatingStandard.id == standard_id).with_for_update(read=True)
    )
    if not standard or not standard.enabled or standard.archived or not standard.is_demo:
        raise HTTPException(409, "演示标准不存在或已停用")
    if (
        not standard.rules
        or not standard.rules.get("indicators")
        or not standard.rules.get("thresholds")
    ):
        raise HTTPException(409, "历史标准明细未留存，不能据此新建申报，请选择完整标准")
    if db.scalar(
        select(RatingApplication.id).where(
            RatingApplication.station_id == station_id,
            RatingApplication.status.in_(["draft", "submitted", "reviewed"]),
        )
    ):
        raise HTTPException(409, "场站存在未结束的星级申请")
    previous = rating_results.latest(db, station_id)
    if kind not in {"initial", "renewal", "reassessment", "appeal"}:
        raise HTTPException(422, "不支持的申报类型")
    if kind == "initial":
        if previous or parent_id is not None or parent_version is not None:
            raise HTTPException(409, "已有评价结果，请使用复评或申诉")
    else:
        if not previous or previous.id != parent_id or previous.version != parent_version:
            raise HTTPException(409, "关联结果已变化，请刷新后重新发起")
        if not note.strip():
            raise HTTPException(422, "请填写复评或申诉理由")
        if kind == "renewal" and (
            previous.valid_until is None or previous.valid_until >= rating_results.local_day()
        ):
            raise HTTPException(409, "结果尚未到期或有效期未确认，不能过期复评")
        if kind == "appeal" and standard_id != previous.standard_id:
            raise HTTPException(422, "申诉沿用原评定标准，新标准请另行复评")
    rating_configuration.reject_platform_overrides(standard.rules, scores)
    frozen_rules = {"code": standard.code, "version": standard.version, **standard.rules}
    context = rating_configuration.capture(db, station_id, standard.rules)
    if context is not None:
        frozen_rules["evaluation_context"] = context
    declared = dict(scores)
    for i in standard.rules["indicators"]:
        if i["source"] == "runtime":
            declared[i["code"]] = None
    calculation = calculate(frozen_rules, declared)
    a = RatingApplication(
        station_id=station_id,
        operator_id=user.operator_id,
        standard_id=standard_id,
        status="draft",
        kind=kind,
        parent_id=parent_id,
        before_result=rating_results.reference(previous) if previous else None,
        initial_scores=declared,
        current_scores=declared,
        calculation=calculation,
        rule_snapshot=frozen_rules,
        evidence_note=note,
    )
    db.add(a)
    db.flush()
    db.add(
        RatingAction(
            application_id=a.id,
            actor_id=user.id,
            action="create",
            transition_snapshot={"before": None, "after": state(a)},
            comment="创建示例自评申请",
            payload=calculation,
        )
    )
    transition(db, user, "ratings.create", a.id, None, state(a), "rating_application")
    return a


def access(db, user, aid, lock=False):
    query = scoped_records(select(RatingApplication), RatingApplication, user).where(
        RatingApplication.id == aid
    )
    if lock:
        # Every mutation locks station before application, including material changes.
        # This serializes publication replacement with new/continued applications.
        station_id = db.scalar(query.with_only_columns(RatingApplication.station_id))
        if station_id is None:
            raise HTTPException(404, "评星申请不存在或不可访问")
        db.scalar(
            station_scope(select(Station), user).where(Station.id == station_id).with_for_update()
        )
    a = db.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "评星申请不存在或不可访问")
    if user.role not in {"operator", "association", "regulator"}:
        raise HTTPException(403, "无权查看评价")
    return a


def edit(db, user, aid, version, scores, note):
    demo_only()
    a = access(db, user, aid, True)
    audit_before = state(a)
    if user.role != "operator" or a.status not in {"draft", "rejected"}:
        raise HTTPException(409, "仅所属运营商可修改草稿或驳回申请")
    if a.version != version:
        raise HTTPException(409, "申请已变化，请刷新后重试")
    rating_configuration.reject_platform_overrides(a.rule_snapshot, scores)
    declared = dict(scores)
    for indicator in a.rule_snapshot["indicators"]:
        if indicator["source"] == "runtime":
            declared[indicator["code"]] = None
    result = calculate(a.rule_snapshot, declared)
    before = {"scores": a.current_scores, "note": a.evidence_note}
    # Initial self-assessment is frozen once submitted, even after rejection.
    if a.status == "draft":
        a.initial_scores = declared
    a.current_scores, a.calculation, a.evidence_note = declared, result, note
    a.version += 1
    db.add(
        RatingAction(
            application_id=a.id,
            actor_id=user.id,
            action="edit",
            transition_snapshot={"before": audit_before, "after": state(a)},
            comment="修改申报自评与依据",
            payload={"before": before, "scores": declared, "note": note, "calculation": result},
        )
    )
    transition(db, user, "ratings.edit", a.id, audit_before, state(a), "rating_application")
    return a


def check_parent(db, a):
    current = rating_results.latest(db, a.station_id)
    if a.parent_id is None:
        if current:
            raise HTTPException(409, "已有其他公示结果，请重新发起复评")
    elif not current or current.id != a.parent_id or current.version != a.before_result["version"]:
        raise HTTPException(409, "原评定结果已变化，请撤回并重新发起")


def act(db, user, aid, action, version, comment, scores, valid_until=None):
    demo_only()
    a = access(db, user, aid, True)
    audit_before = state(a)
    if a.version != version:
        raise HTTPException(409, "评审记录已变化，请刷新")
    if action != "publish" and valid_until is not None:
        raise HTTPException(422, "仅公示动作可设置有效期")
    if action == "submit":
        if user.role != "operator":
            raise HTTPException(403, "仅运营商可提交")
        if a.status not in {"draft", "rejected"}:
            raise HTTPException(409, "当前状态不能提交")
        station = db.scalar(
            station_scope(select(Station), user).where(Station.id == a.station_id).with_for_update()
        )
        if not station or station.status not in {"access_approved", "accepted", "operating"}:
            raise HTTPException(409, "场站已不符合申报条件")
        if db.scalar(
            select(RatingApplication.id).where(
                RatingApplication.station_id == a.station_id,
                RatingApplication.id != a.id,
                RatingApplication.status.in_(["draft", "submitted", "reviewed"]),
            )
        ):
            raise HTTPException(409, "场站另有未结束申报，不能重复提交")
        check_parent(db, a)
        db.execute(
            update(RatingAttachment)
            .where(
                RatingAttachment.application_id == a.id,
                RatingAttachment.submitted_version.is_(None),
            )
            .values(submitted_version=a.version + 1)
        )
        a.status = "submitted"
    elif action == "withdraw":
        if user.role != "operator" or a.status not in {"draft", "submitted"}:
            raise HTTPException(409, "当前角色或状态不允许撤回")
        a.status = "withdrawn"
    elif action in {"review", "reject", "publish", "delist"}:
        if user.role != "association":
            raise HTTPException(403, "仅协会人员可评审与公示")
        if not comment.strip():
            raise HTTPException(422, "请填写评审依据或处理说明")
        if action == "review":
            if a.status not in {"submitted", "reviewed"}:
                raise HTTPException(409, "当前状态不能复核")
            rating_configuration.reject_platform_overrides(a.rule_snapshot, scores)
            merged = {**a.current_scores, **scores}
            result = calculate(a.rule_snapshot, merged)
            if result["missing"]:
                raise HTTPException(422, "仍有缺失指标，请核实证据并补齐评分")
            a.current_scores, a.calculation = merged, result
            a.status = "reviewed"
        elif action == "reject":
            if a.status != "submitted":
                raise HTTPException(409, "仅待评审申请可驳回")
            a.status = "rejected"
        elif action == "publish":
            if a.status != "reviewed" or a.calculation["missing"]:
                raise HTTPException(409, "需完成复核后公示")
            if valid_until is None or valid_until < rating_results.local_day():
                raise HTTPException(422, "请明确填写不早于今天的公示有效期，期限规则待确认")
            check_parent(db, a)
            station = db.get(Station, a.station_id)
            if station.status not in {"access_approved", "accepted", "operating"}:
                raise HTTPException(409, "场站当前不符合公示条件")
            previous = rating_results.latest(db, a.station_id)
            if previous:
                previous_before = state(previous)
                previous.status = "superseded"
                previous.version += 1
                db.add(
                    RatingAction(
                        application_id=previous.id,
                        actor_id=user.id,
                        action="supersede",
                        transition_snapshot={"before": previous_before, "after": state(previous)},
                        comment="新评定结果公示后替代",
                        payload={"replacement_id": a.id, "before": a.before_result},
                    )
                )
                transition(
                    db,
                    user,
                    "ratings.supersede",
                    previous.id,
                    previous_before,
                    {**state(previous), "replacement_id": a.id},
                    "rating_application",
                )
                db.flush()
            a.valid_until = valid_until
            a.published_at = now()
            a.report_snapshot = rating_results.report(db, a, user, a.published_at)
            a.report_snapshot = {**a.report_snapshot, "publication_comment": comment}
            a.status = "published"
        else:
            if a.status != "published":
                raise HTTPException(409, "仅已公示结果可摘牌")
            a.status = "delisted"
    else:
        raise HTTPException(422, "不支持的评价动作")
    a.version += 1
    db.add(
        RatingAction(
            application_id=a.id,
            actor_id=user.id,
            action=action,
            transition_snapshot={"before": audit_before, "after": state(a)},
            comment=comment,
            payload={
                "result": rating_results.reference(a),
                "before_result": a.before_result,
                "scores": a.current_scores,
                "calculation": a.calculation,
                "materials": [
                    {"id": m.id, "sha256": m.sha256, "category": m.category}
                    for m in db.scalars(
                        select(RatingAttachment).where(RatingAttachment.application_id == a.id)
                    )
                ],
            },
        )
    )
    transition(db, user, "ratings." + action, a.id, audit_before, state(a), "rating_application")
    return a


def output(a, station_status=None):
    return {
        "id": a.id,
        "station_id": a.station_id,
        "operator_id": a.operator_id,
        "standard_id": a.standard_id,
        "status": a.status,
        "kind": a.kind,
        "parent_id": a.parent_id,
        "before_result": a.before_result,
        "valid_until": a.valid_until.isoformat() if a.valid_until else None,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "has_report": a.report_snapshot is not None,
        **rating_results.effective(a, station_status=station_status),
        "version": a.version,
        "calculation": a.calculation,
        "is_demo": a.is_demo,
        "evidence_note": a.evidence_note,
        "created_at": a.created_at.isoformat(),
    }
