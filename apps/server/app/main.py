from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.contracts import ErrorResponse, HealthResponse
from app.dependencies import DB, passwords  # noqa: F401 - retained compatibility for seed/tests
from app.openapi import build_schema
from app.routers import (
    accounts,
    alert_configuration,
    alert_exports,
    alert_notifications,
    alert_period_facts,
    alerts,
    application_evidence,
    applications,
    article_assets,
    attachments,
    audits,
    auth,
    business_exports,
    charging_report,
    companies,
    configured_observations,
    content,
    content_images,
    dashboard_operations,
    device_inventory,
    enrollment,
    faults,
    geography,
    hazards,
    inspection_materials,
    inspection_plans,
    inspections,
    order_summary,
    order_time_slots,
    orders,
    phone,
    phone_change,
    protocols,
    public_portal,
    quality,
    rating,
    rating_materials,
    rating_results,
    rating_standards,
    registrations,
    runtime,
    sms_auth,
    staff_applications,
    staff_enrollment,
    station_history,
    stations,
    statistics,
    subsidy,
    subsidy_annual,
    subsidy_device_days,
    subsidy_materials,
    subsidy_statistics,
    sync,
    time_slot_aggregate,
)
from app.routers import (
    passwords as password_routes,
)

app = FastAPI(
    title="RongHub API",
    version="0.3.0",
    responses={
        code: {"model": ErrorResponse, "description": description}
        for code, description in {
            401: "Login required or invalid credentials",
            403: "Capability, scope, CSRF or origin rejected",
            404: "Missing or inaccessible resource",
            409: "State/version conflict or unconfirmed production rule",
            422: "Invalid input; validation details omit submitted values",
            429: "Rate limit; observe Retry-After",
        }.items()
    },
)


def api_schema():
    if app.openapi_schema is None:
        app.openapi_schema = build_schema(app)
    return app.openapi_schema


app.openapi = api_schema
for router in [
    auth.router,
    staff_enrollment.router,
    staff_applications.router,
    sms_auth.router,
    phone.router,
    phone_change.router,
    audits.router,
    accounts.router,
    password_routes.router,
    content.router,
    content_images.router,
    enrollment.router,
    geography.router,
    faults.router,
    registrations.router,
    companies.router,
    alert_configuration.router,
    alert_notifications.router,
    configured_observations.router,
    alert_period_facts.router,
    alert_exports.router,
    alerts.router,
    stations.router,
    station_history.router,
    statistics.router,
    dashboard_operations.router,
    device_inventory.router,
    charging_report.router,
    sync.router,
    time_slot_aggregate.router,
    applications.router,
    application_evidence.router,
    attachments.router,
    article_assets.router,
    business_exports.router,
    subsidy.router,
    subsidy_materials.router,
    subsidy_annual.router,
    subsidy_device_days.router,
    subsidy_statistics.router,
    quality.router,
    rating.router,
    rating_materials.router,
    rating_results.router,
    rating_standards.router,
    runtime.router,
    inspection_plans.router,
    hazards.router,
    protocols.router,
    public_portal.router,
    inspections.router,
    orders.router,
    order_summary.router,
    order_time_slots.router,
    inspection_materials.router,
]:
    app.include_router(router)


@app.middleware("http")
async def private_api_cache(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "不允许的请求来源"})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/v1/health", response_model=HealthResponse)
def health(db: DB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    db.execute(text("SELECT 1"))
    return {"status": "ok", "demo_mode": settings.demo_mode}


@app.exception_handler(RequestValidationError)
async def validation_error(request, exception):
    # Validation responses must not echo passwords or nested credential inputs.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]}
                for e in exception.errors()
            ]
        },
    )
