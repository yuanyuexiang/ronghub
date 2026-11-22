"""Submission receipt dates are distinct from draft creation and subsidy periods."""

from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app.hourly_energy import BEIJING
from app.models import (
    ApprovalAction,
    RatingAction,
    RatingApplication,
    StationApplication,
    SubsidyAction,
    SubsidyClaim,
)


class BusinessDates(BaseModel):
    submitted_from: date | None = None
    submitted_to: date | None = None
    period_from: date | None = None
    period_to: date | None = None

    @model_validator(mode="after")
    def ordered(self):
        for start, end in [
            (self.submitted_from, self.submitted_to),
            (self.period_from, self.period_to),
        ]:
            if start and end and start > end:
                raise ValueError("开始日期不能晚于结束日期")
        if self.submitted_to == date.max:
            raise ValueError("提交结束日期须早于9999-12-31")
        return self


def date_filters(
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
):
    # FastAPI converts dates; turn cross-field validation into a public 422, not 500.
    try:
        return BusinessDates(
            submitted_from=submitted_from,
            submitted_to=submitted_to,
            period_from=period_from,
            period_to=period_to,
        )
    except ValueError as error:
        raise HTTPException(
            422, "日期范围不正确：开始日须不晚于结束日，提交结束日须早于9999-12-31"
        ) from error


Dates = Annotated[BusinessDates, Depends(date_filters)]


def submission_time(model):
    action, fk = {
        SubsidyClaim: (SubsidyAction, SubsidyAction.claim_id),
        RatingApplication: (RatingAction, RatingAction.application_id),
        StationApplication: (ApprovalAction, ApprovalAction.application_id),
    }[model]
    return (
        select(action.created_at)
        .where(fk == model.id, action.action == "submit")
        .order_by(action.id.desc())
        .limit(1)
        .correlate(model)
        .scalar_subquery()
    )


def apply_beijing_days(stmt, column, start, end):
    if start and end and start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if end == date.max:
        raise HTTPException(422, "结束日期须早于9999-12-31")
    if start:
        stmt = stmt.where(column >= datetime.combine(start, time.min, BEIJING))
    if end:
        stmt = stmt.where(column < datetime.combine(end + timedelta(days=1), time.min, BEIJING))
    return stmt


def apply_dates(stmt, model, dates):
    dates = dates or BusinessDates()
    stmt = apply_beijing_days(
        stmt, submission_time(model), dates.submitted_from, dates.submitted_to
    )
    if model is not SubsidyClaim and (dates.period_from or dates.period_to):
        module = "星级申报" if model is RatingApplication else "场站申请"
        raise HTTPException(422, f"{module}不支持补贴申请周期条件")
    if model is SubsidyClaim:
        # Inclusive overlap: a claim spanning the selected period remains visible.
        if dates.period_from:
            stmt = stmt.where(model.end_date >= dates.period_from)
        if dates.period_to:
            stmt = stmt.where(model.start_date <= dates.period_to)
    return stmt
