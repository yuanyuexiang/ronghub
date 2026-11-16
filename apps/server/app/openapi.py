"""Document existing cookie/CSRF enforcement without inventing bearer authentication."""

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.requests import Request

from app.authorization import (
    APPLICATION_ACTIONS,
    CONTENT_ACTIONS,
    INSPECTION_ACTIONS,
    RATING_ACTIONS,
    SUBSIDY_ACTIONS,
    request_capability,
)
from app.dependencies import current_user
from app.enrollment import applicant
from app.staff_applications import applicant as staff_applicant


def depends_on(dependant, target):
    return dependant.call is target or any(
        depends_on(child, target) for child in dependant.dependencies
    )


def build_schema(app):
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema["components"]["securitySchemes"] = {
        "StaffApplicantCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": "ronghub_staff_applicant",
        },
        "BusinessCookie": {"type": "apiKey", "in": "cookie", "name": "ronghub_session"},
        "EnrollmentCookie": {"type": "apiKey", "in": "cookie", "name": "ronghub_enrollment"},
        "CsrfHeader": {"type": "apiKey", "in": "header", "name": "x-csrf-token"},
    }
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        for method in route.methods:
            operation = schema["paths"][route.path_format][method.lower()]
            # FastAPI may inherit HTML success media types for error models. Runtime
            # HTTPException/validation handlers always emit JSON, including report routes.
            for code in ["401", "403", "404", "409", "422", "429"]:
                operation["responses"][code]["content"] = {
                    "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
                }
            operation["responses"]["429"]["headers"] = {
                "Retry-After": {"schema": {"type": "string"}, "description": "Seconds before retry"}
            }
            cookie = None
            if depends_on(route.dependant, current_user):
                cookie = "BusinessCookie"
                request = Request(
                    {
                        "type": "http",
                        "method": method,
                        "path": route.path_format,
                        "query_string": b"",
                        "headers": [],
                    }
                )
                capability = request_capability(request)
                operation["x-capability"] = capability
                if route.path_format == "/api/v1/subsidy/claims/{claim_id}/actions":
                    operation["x-action-capabilities"] = SUBSIDY_ACTIONS
                if route.path_format == "/api/v1/ratings/applications/{application_id}/actions":
                    operation["x-action-capabilities"] = RATING_ACTIONS
                if route.path_format == "/api/v1/inspections/{task_id}/actions":
                    operation["x-action-capabilities"] = INSPECTION_ACTIONS
                if route.path_format == "/api/v1/applications/{application_id}/actions":
                    operation["x-action-capabilities"] = APPLICATION_ACTIONS

                if route.path_format == "/api/v1/content/{article_id}/actions":
                    operation["x-action-capabilities"] = CONTENT_ACTIONS
                if route.path_format == "/api/v1/content/batch-actions":
                    operation["x-action-capabilities"] = {
                        key: CONTENT_ACTIONS[key] for key in ("publish", "delete")
                    }
                if route.path_format.startswith("/api/v1/content"):
                    operation["x-authorization-note"] = (
                        "manage=true requires global content.manage; writes require content.read and the specific action grant; management writes also require content.manage. CSV exports require global regulator content.read + content.manage + content.export. Batch actions commit atomically after all versions match. Audience and reply scope checks apply."
                    )
            elif depends_on(route.dependant, applicant):
                cookie = "EnrollmentCookie"
            elif depends_on(route.dependant, staff_applicant):
                cookie = "StaffApplicantCookie"
            if cookie:
                requirement = {cookie: []}
                if method not in {"GET", "HEAD", "OPTIONS"}:
                    requirement["CsrfHeader"] = []
                operation["security"] = [requirement]
            else:
                operation["security"] = []
            if route.path_format.startswith("/api/v1/auth/staff-enrollment"):
                operation["x-authorization-note"] = (
                    "Anonymous captcha-protected demo-only submission grants no user/session/verified-phone rights. Progress is protected by a separate 24h HttpOnly applicant cookie; no lookup by phone or request ID, no temporary password disclosure."
                )
            if route.path_format.startswith("/api/v1/staff-applications"):
                operation["x-authorization-note"] = (
                    "Global regulator accounts.manage only. Explicit profile/version and district delegation checks; single pending review, mandatory first-password replacement. Contact phones remain unverified; no actual message or credential delivery."
                )
            if route.path_format.startswith("/api/v1/auth/sms"):
                operation["x-authorization-note"] = (
                    "Anonymous OTP authentication: issuance requires one-use browser captcha; eligible and ineligible identifiers receive the same public shape/message. Confirmation requires the purpose-specific browser-bound SMS challenge, current account/phone proof versions and active account. Demo proofs work only in explicit demo transport mode; production provider absence returns 503. Recovery replaces credentials and revokes all sessions, without automatic login."
                )
            if route.path_format.startswith("/api/v1/auth/phone"):
                operation["x-authorization-note"] = (
                    "Personal authenticated account only, independent of module roles and business contacts. CSRF required for writes; issuance revalidates the current password, session and account version. Demo proofs are explicitly tagged and are not production phone proofs; provider absence returns 503."
                )
            if route.path_format == "/api/v1/auth/login":
                operation["x-authorization-note"] = (
                    "Anonymous password login still requires an unconsumed browser-bound captcha_id/captcha_answer from GET /auth/captcha; preserve its HttpOnly cookie. No demo bypass."
                )
            if route.path_format == "/api/v1/auth/captcha":
                operation["x-authorization-note"] = (
                    "Anonymous, rate-limited pre-authentication challenge; PNG image and opaque ID only, no plaintext answer. Refresh invalidates the current browser's earlier unused challenge."
                )
            operation["x-response-contract"] = (
                "validated" if route.response_model else "unspecified"
            )
    return schema
