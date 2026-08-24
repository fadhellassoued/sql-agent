from dataclasses import dataclass
import os

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class AuthenticatedUser:
    """
    Identity established by Google IAP.

    In production, the identity comes from the signed
    X-Goog-IAP-JWT-Assertion header.

    Local development can optionally use a development
    identity when IAP_LOCAL_DEV=true.
    """

    user_id: str
    email: str | None = None


def get_authenticated_user(
    request: Request,
) -> AuthenticatedUser:
    """
    Get the authenticated user.

    Production:
        IAP must be configured and the request should arrive
        through the protected Cloud Run service.

    Development:
        Set IAP_LOCAL_DEV=true and IAP_DEV_USER_EMAIL to test
        locally.

    IMPORTANT:
    In production, plain X-Goog-Authenticated-* headers must
    not be treated as proof of identity. IAP's signed JWT
    should be verified. This function currently provides the
    application boundary; full JWT verification is the next
    security sub-step before production deployment.
    """

    local_dev = (
        os.getenv(
            "IAP_LOCAL_DEV",
            "false",
        ).lower()
        == "true"
    )

    if local_dev:
        email = os.getenv(
            "IAP_DEV_USER_EMAIL",
            "local-dev@localhost",
        )

        return AuthenticatedUser(
            user_id=email,
            email=email,
        )

    email_header = request.headers.get(
        "x-goog-authenticated-user-email"
    )

    user_id_header = request.headers.get(
        "x-goog-authenticated-user-id"
    )

    jwt_assertion = request.headers.get(
        "x-goog-iap-jwt-assertion"
    )

    if not jwt_assertion:
        raise HTTPException(
            status_code=401,
            detail="IAP authentication required.",
        )

    if not user_id_header:
        raise HTTPException(
            status_code=401,
            detail="Authenticated user identity missing.",
        )

    email = None

    if email_header:
        email = email_header.split(
            ":", 1
        )[-1]

    user_id = user_id_header.split(
        ":",
        1,
    )[-1]

    return AuthenticatedUser(
        user_id=user_id,
        email=email,
    )