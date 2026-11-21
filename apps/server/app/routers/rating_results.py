"""Scoped publication search and private, frozen HTML report downloads."""

import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from app import rating, rating_results
from app.dependencies import DB, Actor, SnapshotActor
from app.models import Audit, Operator, RatingApplication, Station
from app.query_contracts import RatingPublicationPage
from app.reporting import SnapshotDB
from app.scopes import records

router = APIRouter(prefix="/api/v1/ratings", tags=["ratings"])


@router.get("/publications", response_model=RatingPublicationPage)
def publications(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query(default="", max_length=100),
    result_state: str = "",
    station_type: str = "",
    station_id: int | None = Query(default=None, ge=1, le=9223372036854775807),
    stars: int | None = Query(default=None, ge=0, le=5),
    page: int = Query(default=1, ge=1, le=2147483647),
    page_size: int = Query(default=12, ge=1, le=100),
):
    if result_state not in {
        "",
        "valid",
        "expired",
        "delisted",
        "validity_unknown",
        "inactive",
        "evaluating",
    }:
        raise HTTPException(422, "不支持的结果状态")
    if station_type not in {"", "charging", "swapping", "mixed", "unknown"}:
        raise HTTPException(422, "不支持的场站种类")
    response.headers["Cache-Control"] = "no-store"
    today = rating_results.local_day()
    ranked = (
        records(
            select(
                RatingApplication.id,
                func.row_number()
                .over(
                    partition_by=RatingApplication.station_id,
                    order_by=(
                        case((RatingApplication.status.in_(["published", "delisted"]), 0), else_=1),
                        func.coalesce(
                            RatingApplication.published_at, RatingApplication.created_at
                        ).desc(),
                        RatingApplication.id.desc(),
                    ),
                )
                .label("position"),
            ),
            RatingApplication,
            user,
        )
        .where(RatingApplication.status.in_(["published", "delisted", "submitted", "reviewed"]))
        .subquery()
    )
    a = aliased(RatingApplication)
    state = case(
        (a.status == "delisted", "delisted"),
        (a.status.in_(["submitted", "reviewed"]), "evaluating"),
        (~Station.status.in_(["access_approved", "accepted", "operating"]), "inactive"),
        (a.valid_until.is_(None), "validity_unknown"),
        (a.valid_until < today, "expired"),
        else_="valid",
    )
    effective_stars = case(
        (state.in_(["delisted", "inactive", "expired"]), 0),
        (state == "valid", a.calculation["stars"].as_integer()),
        else_=None,
    )
    stmt = (
        select(a, Station, Operator)
        .join(ranked, ranked.c.id == a.id)
        .join(Station, Station.id == a.station_id)
        .join(Operator, Operator.id == a.operator_id)
        .where(ranked.c.position == 1)
    )
    if station_type:
        stmt = stmt.where(Station.station_type == station_type)
    if station_id is not None:
        stmt = stmt.where(Station.id == station_id)
    if q.strip():
        literal = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            or_(
                Station.name.ilike(f"%{literal}%", escape="\\"),
                Operator.name.ilike(f"%{literal}%", escape="\\"),
            )
        )
    if result_state:
        stmt = stmt.where(state == result_state)
    if stars is not None:
        stmt = stmt.where(effective_stars == stars)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = []
    for app, station, operator in db.execute(
        stmt.order_by(a.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ):
        items.append(
            {
                "id": app.id,
                "station_id": station.id,
                "station_type": station.station_type,
                "station_name": station.name,
                "operator_name": operator.name,
                "district": station.district,
                "address": station.address,
                "kind": app.kind,
                "is_demo": app.is_demo,
                "valid_until": app.valid_until.isoformat() if app.valid_until else None,
                "published_at": app.published_at.isoformat() if app.published_at else None,
                **rating_results.effective(app, today=today, station_status=station.status),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "scope": "当前账号授权场站；非匿名公众入口",
        "timezone": "Asia/Shanghai",
    }


KINDS = {
    "initial": "初次评星",
    "renewal": "过期评星",
    "reassessment": "复评 / 升降级",
    "appeal": "异议申诉",
}
ACTIONS = {
    "create": "创建申报",
    "edit": "修改申报",
    "submit": "提交申报",
    "withdraw": "撤回申请",
    "review": "人工复核",
    "reject": "驳回补正",
    "publish": "结果公示",
    "delist": "摘牌",
    "supersede": "被新结果替代",
}


def display_time(value):
    return (
        datetime.fromisoformat(value)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def escaped(value):
    return html.escape(str(value if value is not None else "未确认"), quote=True)


@router.get("/applications/{application_id}/report", response_class=HTMLResponse)
def report(application_id: int, user: Actor, db: DB):
    a = rating.access(db, user, application_id)
    data = a.report_snapshot
    if not data:
        raise HTTPException(409, "尚无公示时冻结的报告；历史结果不得伪造补录报告")
    station = data["station"]
    rows = "".join(
        f"<tr><td>{escaped(i['name'])}</td><td>{escaped(data['initial_scores'].get(i['code']))}</td><td>{escaped(i.get('raw_value'))}</td><td>{escaped(i.get('band_label'))}</td><td>{escaped(i.get('effective_from'))}</td><td>{'不参与' if i.get('enabled') is False else escaped(i.get('score'))}</td><td>{escaped(i.get('max_score'))}</td><td>{escaped(i.get('weight'))}</td><td>{'停用，不参与' if i.get('enabled') is False else '启用' if i.get('enabled') is True else '历史状态未留存'}</td><td>{escaped(i.get('normalization_weight'))}</td><td>{'平台数据待核验' if i['source'] == 'runtime' else '人工材料评分'}</td></tr>"
        for i in data["calculation"]["details"]
    )
    groups = "".join(
        f"<li>{escaped(g['name'])}：{escaped(g['score'])}分（分类权重{escaped(g['weight'])}）</li>"
        for g in data["calculation"].get("categories", [])
    )
    history = "".join(
        f"<li>{escaped(display_time(h['created_at']))} · {escaped(h['actor'])} · {escaped(ACTIONS.get(h['action'], h['action']))}<p>{escaped(h['comment'])}</p></li>"
        for h in data["history"]
    )
    before = data["before_result"]
    previous = (
        f"原结果 RATE-{escaped(before['id'])}：{escaped(before['stars'])}星，有效期至{escaped(before['valid_until'])}"
        if before
        else "首次评定，无历史公示结果"
    )
    snapshot = escaped(json.dumps(data, ensure_ascii=False, indent=2))
    db.add(
        Audit(
            user_id=user.id,
            action="ratings.report.download",
            object_id=a.id,
            detail={"application_id": a.id, "report_id": data["report_id"]},
        )
    )
    db.commit()
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escaped(data["report_id"])} 评价报告</title>
<style>body{{font:15px/1.8 system-ui,sans-serif;color:#24364a;background:#f2f5f9;margin:0}}main{{max-width:850px;margin:40px auto;background:white;padding:48px;border-top:6px solid #245c89}}h1{{margin:0}}.label{{letter-spacing:3px;color:#245c89}}.notice{{padding:12px;background:#fff4db;border:1px solid #ebd59b}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #dde5ef;padding:10px;text-align:left;overflow-wrap:anywhere;font-size:13px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}}p{{white-space:pre-wrap}}footer{{color:#64758b;border-top:1px solid #ddd;margin-top:30px}}@media print{{body{{background:white}}main{{margin:0;padding:12mm;max-width:none}}details{{display:none}}h2,thead{{break-after:avoid}}tr{{break-inside:avoid}}}}</style>
<main><div class="label">RONGHUB · 演示评价档案</div><h1>充电场站星级评价报告</h1><p>{escaped(data["report_id"])}</p><p class="notice">{escaped(data["notice"])}</p>
<h2>场站与评定</h2><p>{escaped(station["name"])}<br>运营企业：{escaped(station["operator"])}<br>区域：{escaped(station["district"])}<br>地址：{escaped(station["address"])}</p>
<p>公示时间（北京时间）：{escaped(display_time(data["published_at"]))}<br>有效期至：{escaped(data["valid_until"])}（北京时间日期，当日有效）<br>公示办理人：{escaped(data["published_by"])}<br>类型：{escaped(KINDS.get(data["kind"], data["kind"]))}</p>
<p>{previous}</p><h2>评分与结论</h2><p>总分：{escaped(data["calculation"]["total"])} · 评定星级：{escaped(data["calculation"]["stars"])}星</p><ul>{groups}</ul>
<table><thead><tr><th>指标</th><th>首次人工原值</th><th>复核字段原值</th><th>映射档位</th><th>指标生效日</th><th>复核分</th><th>满分</th><th>配置权重</th><th>指标状态</th><th>启用权重之和</th><th>来源</th></tr></thead><tbody>{rows}</tbody></table><h2>申报与公示依据</h2><p>{escaped(data["evidence_note"])}</p><p>{escaped(data["publication_comment"])}</p>
<h2>评审留痕（北京时间）</h2><ol>{history}</ol><details><summary>规则、原始分与材料摘要快照</summary><pre>{snapshot}</pre></details>
<footer>本报告保留公示时快照，不代表当前仍有效。最新星级、到期或摘牌状态请查平台公示查询。正式报告模板及签章待确认。</footer></main></html>""",
        headers={
            "Content-Disposition": f'attachment; filename="ronghub-rating-{a.id}.html"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
