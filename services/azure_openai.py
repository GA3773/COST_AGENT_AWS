"""Azure OpenAI client factory with hybrid authentication.

Authentication flow (matching EMR-MANAGER pattern):
1. Service Principal authenticates with Azure AD using PEM certificate
2. Azure AD returns an access token
3. Access token is sent as Bearer token in Authorization header
4. OpenAI API key is also sent for authentication
5. A fresh client with fresh token is created for each graph invocation
"""

import os
from datetime import datetime

from azure.identity import CertificateCredential
from langchain_openai import AzureChatOpenAI

from config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_PEM_PATH,
    AZURE_SPN_CLIENT_ID,
    AZURE_TENANT_ID,
    AZURE_USER_ID,
    get_logger,
)

logger = get_logger(__name__)

# Cached credential object (thread-safe, handles token caching internally)
_credential = None


def _get_credential() -> CertificateCredential | None:
    """Get or create the CertificateCredential for Azure AD authentication."""
    global _credential
    if _credential is not None:
        return _credential

    if not AZURE_TENANT_ID or not AZURE_SPN_CLIENT_ID:
        logger.warning("Azure Service Principal credentials not configured")
        return None

    if not os.path.exists(AZURE_PEM_PATH):
        logger.warning(f"PEM certificate not found at {AZURE_PEM_PATH}")
        return None

    try:
        _credential = CertificateCredential(
            tenant_id=AZURE_TENANT_ID,
            client_id=AZURE_SPN_CLIENT_ID,
            certificate_path=AZURE_PEM_PATH,
        )
        logger.info("CertificateCredential created successfully")
        return _credential
    except Exception as e:
        logger.error(f"Failed to create CertificateCredential: {e}")
        return None


def _get_bearer_token() -> str | None:
    """Get a fresh Azure AD access token for cognitive services."""
    credential = _get_credential()
    if not credential:
        return None

    try:
        token_response = credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        logger.info(
            f"Azure AD token obtained, expires at "
            f"{datetime.fromtimestamp(token_response.expires_on).isoformat()}"
        )
        return token_response.token
    except Exception as e:
        logger.error(f"Failed to get Azure AD token: {e}")
        return None


def create_llm() -> AzureChatOpenAI:
    """Create an AzureChatOpenAI instance with hybrid authentication.

    Uses Service Principal + PEM certificate for Bearer token when available,
    falls back to API key only authentication otherwise.

    Returns a fresh LLM instance with a fresh token (call this before each
    graph invocation to avoid token expiry during long-running workflows).
    """
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
        raise ValueError(
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set"
        )

    # Build default headers
    default_headers = {"x-ms-useragent": AZURE_USER_ID}

    # Try hybrid auth: Bearer token + API key
    bearer_token = _get_bearer_token()
    if bearer_token:
        default_headers["Authorization"] = f"Bearer {bearer_token}"
        logger.info("Creating AzureChatOpenAI with hybrid auth (Bearer + API key)")
    else:
        logger.info("Creating AzureChatOpenAI with API key auth only")

    llm = AzureChatOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT,
        default_headers=default_headers,
        temperature=0,
    )

    return llm
