import secrets
from typing import Dict, Optional

from google_auth_oauthlib.flow import Flow

from google.oauth2.credentials import Credentials
from google.oauth2 import id_token

from google.auth.transport.requests import (
    Request as GoogleAuthRequest,
)

from src.config import settings


# ============================================================
# OAUTH SCOPES
# ============================================================

GOOGLE_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/bigquery",
]


# ============================================================
# LOCAL SERVER-SIDE OAUTH STORE
# ============================================================
#
# LOCAL DEMO ONLY.
#
# Production:
#   Redis / Memorystore + encrypted credential storage.
#
# OAuth tokens are NEVER returned to React.
# ============================================================

_oauth_sessions: Dict[str, dict] = {}


# ============================================================
# GOOGLE CLIENT CONFIG
# ============================================================

def get_google_client_config() -> dict:

    return {
        "web": {
            "client_id":
                settings.GOOGLE_OAUTH_CLIENT_ID,

            "client_secret":
                settings.GOOGLE_OAUTH_CLIENT_SECRET,

            "auth_uri":
                "https://accounts.google.com/o/oauth2/auth",

            "token_uri":
                "https://oauth2.googleapis.com/token",

            "redirect_uris": [
                settings.GOOGLE_OAUTH_REDIRECT_URI
            ],
        }
    }


# ============================================================
# CREATE OAUTH FLOW
# ============================================================

def create_oauth_flow(
    state: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> Flow:
    """
    Create Google's OAuth Flow.

    The same PKCE code_verifier generated during login MUST
    be provided again during the callback/token exchange.
    """

    flow = Flow.from_client_config(
        get_google_client_config(),

        scopes=GOOGLE_OAUTH_SCOPES,

        state=state,

        code_verifier=code_verifier,

        # We generate and persist our own verifier.
        autogenerate_code_verifier=False,
    )

    flow.redirect_uri = (
        settings.GOOGLE_OAUTH_REDIRECT_URI
    )

    return flow


# ============================================================
# GENERATE PKCE CODE VERIFIER
# ============================================================

def generate_code_verifier() -> str:
    """
    PKCE code verifier.

    token_urlsafe(64) gives us a strong verifier within the
    PKCE allowed size range.
    """

    return secrets.token_urlsafe(64)


# ============================================================
# CREATE AUTHORIZATION URL
# ============================================================

def create_authorization_url(
    code_verifier: str,
):
    """
    Start Google authorization using a known PKCE verifier.

    The caller stores the verifier in the signed server-side
    browser session until the callback arrives.
    """

    flow = create_oauth_flow(
        code_verifier=code_verifier
    )

    authorization_url, state = (
        flow.authorization_url(
            access_type="offline",

            include_granted_scopes="true",

            prompt="consent",
        )
    )

    return (
        authorization_url,
        state,
    )


# ============================================================
# STORE AUTHENTICATED GOOGLE USER
# ============================================================

def store_oauth_credentials(
    credentials: Credentials,
) -> tuple[str, dict]:

    if not credentials.id_token:

        raise ValueError(
            "Google OAuth did not return an ID token."
        )

    # --------------------------------------------------------
    # Cryptographically verify Google's ID token.
    # --------------------------------------------------------

    identity = id_token.verify_oauth2_token(
        credentials.id_token,

        GoogleAuthRequest(),

        settings.GOOGLE_OAUTH_CLIENT_ID,
    )

    email = identity.get(
        "email"
    )

    google_user_id = identity.get(
        "sub"
    )

    email_verified = identity.get(
        "email_verified",
        False,
    )

    if (
        not email
        or not google_user_id
    ):

        raise ValueError(
            "Unable to determine Google user identity."
        )

    if not email_verified:

        raise ValueError(
            "Google account email is not verified."
        )

    # --------------------------------------------------------
    # Create opaque server-side session ID.
    # --------------------------------------------------------

    oauth_session_id = (
        secrets.token_urlsafe(32)
    )

    _oauth_sessions[
        oauth_session_id
    ] = {
        "credentials":
            credentials,

        "user": {
            "user_id":
                google_user_id,

            "email":
                email,
        },
    }

    return (
        oauth_session_id,

        {
            "user_id":
                google_user_id,

            "email":
                email,
        },
    )


# ============================================================
# GET OAUTH SESSION
# ============================================================

def get_oauth_session(
    oauth_session_id: Optional[str],
):

    if not oauth_session_id:

        return None

    return _oauth_sessions.get(
        oauth_session_id
    )


# ============================================================
# GET GOOGLE CREDENTIALS
# ============================================================

def get_oauth_credentials(
    oauth_session_id: Optional[str],
) -> Optional[Credentials]:

    session = get_oauth_session(
        oauth_session_id
    )

    if not session:

        return None

    credentials = session[
        "credentials"
    ]

    # --------------------------------------------------------
    # Refresh expired access token if possible.
    # --------------------------------------------------------

    if (
        credentials.expired
        and credentials.refresh_token
    ):

        credentials.refresh(
            GoogleAuthRequest()
        )

    return credentials


# ============================================================
# GET GOOGLE USER
# ============================================================

def get_oauth_user(
    oauth_session_id: Optional[str],
):

    session = get_oauth_session(
        oauth_session_id
    )

    if not session:

        return None

    return session[
        "user"
    ]


# ============================================================
# DELETE SESSION
# ============================================================

def delete_oauth_session(
    oauth_session_id: Optional[str],
):

    if not oauth_session_id:

        return

    _oauth_sessions.pop(
        oauth_session_id,
        None,
    )