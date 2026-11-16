"""Current-station district scope; None is all districts, [] is no station access."""

from fastapi import HTTPException
from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.models import Operator, Station, User


def districts(user):
    return getattr(user, "allowed_districts", None)


def require_global(user):
    if districts(user) is not None:
        raise HTTPException(403, "该操作影响全域共享配置，当前区域账号无权执行")


def station_scope(stmt, user):
    if user.role == "operator":
        if user.operator_id is None:
            raise HTTPException(403, "未关联运营商")
        stmt = stmt.where(Station.operator_id == user.operator_id)
    elif user.role not in {"regulator", "association"}:
        raise HTTPException(403, "无权访问")
    if districts(user) is not None:
        stmt = stmt.where(Station.district.in_(districts(user)))
    return stmt


def station_ids(user):
    return station_scope(select(Station.id), user)


def records(stmt, model, user):
    return stmt.where(model.station_id.in_(station_ids(user)))


def company_scope(stmt, user):
    if user.role == "operator":
        return stmt.where(Operator.id == user.operator_id)
    if user.role not in {"regulator", "association"}:
        raise HTTPException(403, "无权访问")
    if districts(user) is not None:
        stmt = stmt.where(Operator.id.in_(station_scope(select(Station.operator_id), user)))
    return stmt


def validate_delegation(issuer, allowed):
    own = districts(issuer)
    if own is not None and (allowed is None or not set(allowed).issubset(own)):
        raise HTTPException(403, "不能下发超出自身范围的区域权限")


def accounts_scope(stmt, user):
    if districts(user) is not None:
        visible_companies = company_scope(select(Operator.id), user)
        stmt = stmt.where(
            User.allowed_districts.is_not(None),
            cast(User.allowed_districts, JSONB).contained_by(districts(user)),
            or_(User.operator_id.is_(None), User.operator_id.in_(visible_companies)),
        )
    return stmt


def check_global_request(request, user):
    from app.authorization import management_requested

    if districts(user) is None:
        return
    parts = request.url.path.removeprefix("/api/v1/").strip("/").split("/")
    head = parts[0]
    read = request.method in {"GET", "HEAD", "OPTIONS"}
    global_operation = (
        head in {"sync", "registrations", "staff-applications"}
        or (head == "roles" and not read)
        or (head == "companies" and not read)
        or (head == "subsidy" and len(parts) > 1 and parts[1] == "policies" and not read)
        or (head == "ratings" and len(parts) > 1 and parts[1] == "standards" and not read)
        or (head == "alerts" and len(parts) > 1 and parts[1] == "rules" and not read)
        or (
            head == "content"
            and (
                (not read and not (len(parts) >= 3 and parts[2] == "replies"))
                or management_requested(request)
            )
        )
    )
    if global_operation:
        require_global(user)
