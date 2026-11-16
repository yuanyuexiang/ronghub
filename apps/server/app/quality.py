"""Current authorized station cohorts; observed state composition is not a formal quality rate."""

from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import String, cast, distinct, func, or_, select

from app import runtime
from app.models import FaultEvent, Operator, RuntimeInterval, Station
from app.scopes import station_scope

NOTICE = "按当前授权且来源相同的场站台账及当前区域/企业归属分组，包括未收到观测的场站，不反推历史营业集合。设备本级与单枪分别统计；对象数仅为窗口内有运行区间的源引用数，不是设备总数。对象×查询时长只用于该观测集合的已知/明确未知/未上送时长，不能代表全部设备覆盖。连接/业务轴及演示标识分开，状态占比仅以本轴已上送时长（含未知）为分母。故障独立统计本组所有层级、发生于窗口内的源事件；只对已恢复样本算完整恢复经过秒数，不是维修工时。未认证源完整性，不发布正式在线率、故障率或利用率。"


def inventory(user, source=None, q="", district="", operator_id=None, group_by="district"):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看运行质量观测")
    key, label = {
        "district": (Station.district, Station.district),
        "operator": (cast(Operator.id, String), Operator.name),
        "station": (cast(Station.id, String), Station.name),
    }[group_by]
    stmt = station_scope(
        select(
            Station.id,
            Station.operator_id,
            Station.source,
            key.label("group_key"),
            label.label("group_label"),
        ).join(Operator, Operator.id == Station.operator_id),
        user,
    )
    if source is not None:
        stmt = stmt.where(Station.source == source)
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if district:
        stmt = stmt.where(Station.district == district)
    if operator_id is not None:
        stmt = stmt.where(Station.operator_id == operator_id)
    return stmt


def sources(db, user, q, district, operator_id, page, page_size):
    base = inventory(user, district=district, operator_id=operator_id).subquery()
    stmt = (
        select(base.c.source, func.count().label("station_count"))
        .where(base.c.source != "")
        .group_by(base.c.source)
    )
    if q.strip():
        stmt = stmt.where(base.c.source.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(base.c.source).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [dict(row._mapping) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def report(db, user, params, page, page_size, *, exporting=False):
    selected = inventory(
        user,
        params["source"],
        params["q"],
        params["district"],
        params["operator_id"],
        params["group_by"],
    ).subquery()
    selected_count = db.scalar(select(func.count()).select_from(selected))
    groups = select(
        selected.c.group_key, selected.c.group_label, func.count().label("station_count")
    ).group_by(selected.c.group_key, selected.c.group_label)
    total = db.scalar(select(func.count()).select_from(groups.subquery()))
    if exporting and total > 10000:
        raise HTTPException(422, "单次质量观测导出最多10000行，请缩小范围")
    requested = groups.order_by(selected.c.group_label, selected.c.group_key)
    if not exporting:
        requested = requested.offset((page - 1) * page_size).limit(page_size)
    group_rows = list(db.execute(requested))
    keys = [row.group_key for row in group_rows]
    start, end = params["start_at"], params["end_at"]
    intervals = (
        select(RuntimeInterval, selected.c.group_key)
        .join(
            selected,
            (selected.c.id == RuntimeInterval.station_id)
            & (selected.c.operator_id == RuntimeInterval.operator_id),
        )
        .where(
            selected.c.group_key.in_(keys),
            RuntimeInterval.source == params["source"],
            RuntimeInterval.started_at < end,
            RuntimeInterval.ended_at > start,
        )
    )
    intervals = intervals.where(
        RuntimeInterval.connector_external_id == ""
        if params["asset_scope"] == "equipment"
        else RuntimeInterval.connector_external_id != ""
    )
    base = intervals.subquery()
    clipped = func.extract(
        "epoch", func.least(base.c.ended_at, end) - func.greatest(base.c.started_at, start)
    )
    durations = list(
        db.execute(
            select(
                base.c.group_key,
                base.c.is_demo,
                base.c.dimension,
                base.c.state,
                func.sum(clipped).label("seconds"),
            ).group_by(base.c.group_key, base.c.is_demo, base.c.dimension, base.c.state)
        )
    )
    objects = (
        select(
            base.c.group_key,
            base.c.is_demo,
            base.c.station_id,
            base.c.equipment_external_id,
            base.c.connector_external_id,
        )
        .distinct()
        .subquery()
    )
    observed = list(
        db.execute(
            select(
                objects.c.group_key,
                objects.c.is_demo,
                func.count().label("objects"),
                func.count(distinct(objects.c.station_id)).label("stations"),
            ).group_by(objects.c.group_key, objects.c.is_demo)
        )
    )
    faults = (
        select(FaultEvent, selected.c.group_key)
        .join(
            selected,
            (selected.c.id == FaultEvent.station_id)
            & (selected.c.operator_id == FaultEvent.operator_id),
        )
        .where(
            selected.c.group_key.in_(keys),
            FaultEvent.source == params["source"],
            FaultEvent.started_at >= start,
            FaultEvent.started_at < end,
        )
        .subquery()
    )
    fault_rows = list(
        db.execute(
            select(
                faults.c.group_key,
                faults.c.is_demo,
                func.count().label("observed"),
                func.count(distinct(faults.c.station_id)).label("stations"),
                func.count().filter(faults.c.status == "active").label("active"),
                func.count().filter(faults.c.status == "recovered").label("recovered"),
                func.count().filter(faults.c.status == "unknown").label("unknown"),
                func.avg(func.extract("epoch", faults.c.recovered_at - faults.c.started_at))
                .filter(faults.c.status == "recovered")
                .label("average"),
            ).group_by(faults.c.group_key, faults.c.is_demo)
        )
    )
    by_duration = defaultdict(list)
    observed_map = {(row.group_key, row.is_demo): row for row in observed}
    fault_map = {(row.group_key, row.is_demo): row for row in fault_rows}
    flags = defaultdict(set)
    for row in durations:
        by_duration[(row.group_key, row.is_demo)].append(row)
        flags[row.group_key].add(row.is_demo)
    for row in fault_rows:
        flags[row.group_key].add(row.is_demo)
    window = runtime.seconds(end - start)
    items = []
    for group in group_rows:
        streams = []
        for demo in sorted(flags[group.group_key]):
            identity = (group.group_key, demo)
            measured = observed_map.get(identity)
            fault = fault_map.get(identity)
            object_count = measured.objects if measured else 0
            matrix = window * object_count
            streams.append(
                {
                    "is_demo": demo,
                    "observed_object_count": object_count,
                    "stations_with_runtime": measured.stations if measured else 0,
                    "observed_object_window_seconds": str(matrix),
                    "axes": runtime.build_axes(by_duration[identity], matrix),
                    "faults": {
                        "observed_events": fault.observed if fault else 0,
                        "stations_with_events": fault.stations if fault else 0,
                        "active_events": fault.active if fault else 0,
                        "recovered_events": fault.recovered if fault else 0,
                        "unknown_events": fault.unknown if fault else 0,
                        "average_recovery_elapsed_seconds": str(fault.average)
                        if fault and fault.average is not None
                        else None,
                    },
                }
            )
        items.append(
            {
                "key": group.group_key,
                "label": group.group_label or "未标区域",
                "station_count": group.station_count,
                "streams": streams,
            }
        )
    if exporting and sum(max(1, len(item["streams"])) for item in items) > 10000:
        raise HTTPException(422, "单次质量观测导出最多10000行，请缩小范围")
    return {
        "source": params["source"],
        "group_by": params["group_by"],
        "asset_scope": params["asset_scope"],
        "q": params["q"],
        "district": params["district"],
        "operator_id": params["operator_id"],
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "window_seconds": str(window),
        "selected_station_count": selected_count,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_completeness_verified": False,
        "notice": NOTICE,
    }
