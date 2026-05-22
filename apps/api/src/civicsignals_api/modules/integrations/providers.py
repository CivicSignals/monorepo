"""Provider abstraction + registry for the integrations framework (K1).

This is the seam K2 (Salesforce), K3 (HubSpot) and L1 (Slack) plug into. A
provider implements the outbound lifecycle:

    connect (OAuth authorize URL + token exchange) → refresh-token → push →
    scope-aware error mapping

K1 ships:

- :class:`OAuthConfig` — the per-provider OAuth2 authorization-code config
  (auth/token URLs, default scopes, client id/secret pulled from settings).
- :class:`IntegrationProvider` — the ABC concrete providers subclass.
- :class:`PushRequest` / :class:`PushResult` — the push contract; a result
  carries the typed :class:`~.models.PushErrorCode` so the push runner and the
  K5 recovery UI branch uniformly across providers.
- :class:`ProviderError` — raised by a provider's ``push``/``exchange_code``;
  carries the typed error code so the runner can decide retry vs dead-letter.
- :func:`map_http_status_to_error_code` — the default HTTP-status → typed-error
  mapping providers reuse.
- :data:`REGISTRY` + :func:`register_provider` / :func:`get_provider` — the
  lookup the routes/services use to resolve a provider by its enum kind.

The HTTP layer is injectable: every provider takes an ``httpx.AsyncClient`` so
tests mock the transport (``httpx.MockTransport``) without monkeypatching.

Real Salesforce/HubSpot/Slack clients land in K2/K3/L1; K1 registers only a
``# TODO`` stub (:class:`_StubOAuthProvider`) so the framework + tests run.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from civicsignals_api.config import Settings

from .models import IntegrationProviderKind, PushErrorCode


class ProviderError(Exception):
    """A provider operation failed with a scope-aware, typed error code (K1).

    Concrete providers raise this from :meth:`IntegrationProvider.push` (and the
    OAuth methods) instead of leaking ``httpx``/vendor exceptions, so the push
    runner can map the failure to the push-log's typed ``error_code`` and decide
    retry vs dead-letter (K5). ``provider_response_id`` is the vendor's
    request/trace id when available (surfaced in the recovery UI, doc 08 §3.6).
    """

    def __init__(
        self,
        code: PushErrorCode,
        message: str,
        *,
        provider_response_id: str | None = None,
        response: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider_response_id = provider_response_id
        self.response = response


def map_http_status_to_error_code(status_code: int) -> PushErrorCode:
    """Map an HTTP status to the provider-agnostic typed error code (K1).

    The default mapping concrete providers reuse (and may refine for vendor
    quirks). 401 → ``auth`` (re-auth), 403 → ``permission`` (scope), 404 →
    ``not_found``, 422/400/409 → ``validation``, 429 → ``rate_limited``, 5xx →
    ``transient``; anything else → ``unknown``.
    """
    if status_code == 401:
        return PushErrorCode.AUTH
    if status_code == 403:
        return PushErrorCode.PERMISSION
    if status_code == 404:
        return PushErrorCode.NOT_FOUND
    if status_code == 429:
        return PushErrorCode.RATE_LIMITED
    if status_code in (400, 409, 422):
        return PushErrorCode.VALIDATION
    if 500 <= status_code < 600:
        return PushErrorCode.TRANSIENT
    return PushErrorCode.UNKNOWN


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """A provider's OAuth2 authorization-code configuration (K1).

    ``authorize_url`` / ``token_url`` are the provider's endpoints; ``scopes``
    is what we request at consent; ``client_id`` / ``client_secret`` come from
    settings. ``configured`` is False when the operator has not supplied the
    client credentials, so the route can return a clear 422 rather than starting
    a doomed OAuth flow.
    """

    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id: str | None
    client_secret: str | None
    # Extra static params appended to the authorize URL (e.g. ``access_type`` or
    # provider-specific knobs). Providers override per their docs.
    extra_authorize_params: dict[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """True iff both client id and secret are present (operator wired it up)."""
        return bool(self.client_id) and bool(self.client_secret)


@dataclass(frozen=True, slots=True)
class TokenSet:
    """The OAuth tokens returned by an authorize-code exchange or refresh (K1)."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    # Non-secret external account metadata to persist on the connection
    # (instance_url, team id/name, …). Never contains tokens.
    provider_account: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PushRequest:
    """A request to push one source object to a provider target (K1).

    ``payload`` is the already-mapped, secret-free body (the provider client
    shapes the vendor request from it). ``idempotency_key`` ties repeated pushes
    of the same source object together so K4 can upsert (``# TODO K4``).
    ``external_id`` is the prior provider-side id when re-pushing (upsert).
    """

    target: str
    payload: dict[str, object]
    idempotency_key: str | None = None
    external_id: str | None = None


@dataclass(frozen=True, slots=True)
class PushResult:
    """The outcome of a successful provider push (K1).

    ``external_id`` is the provider-side object id (drives K4 idempotency).
    ``response`` is the redacted provider response stored in the push-log.
    """

    external_id: str | None
    response: dict[str, object] = field(default_factory=dict)
    provider_response_id: str | None = None


class IntegrationProvider(abc.ABC):
    """The outbound-integration provider contract (K1).

    Concrete providers (K2 Salesforce, K3 HubSpot, L1 Slack) subclass this and
    register themselves via :func:`register_provider`. The HTTP layer is
    injected (``http``) so tests mock it. Implementations must NOT log token
    material (threat-model §4.2).
    """

    kind: IntegrationProviderKind
    #: Whether this provider authenticates via OAuth2 (False = API-key/secret,
    #: e.g. the generic webhook provider — no authorize/token flow).
    is_oauth: bool = True

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    @abc.abstractmethod
    def oauth_config(self) -> OAuthConfig:
        """Return this provider's OAuth2 config (auth/token URLs, scopes, client).

        Non-OAuth providers (``is_oauth = False``) may raise ``NotImplementedError``.
        """

    @abc.abstractmethod
    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        """Exchange an authorization ``code`` for tokens (the OAuth callback).

        Raises :class:`ProviderError` (code ``auth``/``transient``) on failure.
        """

    @abc.abstractmethod
    async def refresh(self, *, refresh_token: str) -> TokenSet:
        """Exchange a ``refresh_token`` for a fresh access token.

        Raises :class:`ProviderError` (code ``auth``) when the refresh token is
        itself invalid/expired — the caller marks the connection ``needs_reauth``.
        """

    @abc.abstractmethod
    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        """Push one object to the provider, returning its external id.

        Raises :class:`ProviderError` with a scope-aware code on failure so the
        runner can map it to the push-log and decide retry vs dead-letter.
        """


# --- Generic OAuth2 building block ------------------------------------------


class OAuth2AuthorizationCodeMixin:
    """Reusable OAuth2 authorization-code token exchange/refresh (K1).

    Salesforce/HubSpot/Slack are all standard OAuth2 authorization-code; this
    mixin implements ``exchange_code`` / ``refresh`` over the injected
    ``httpx.AsyncClient`` so concrete providers only declare their
    :class:`OAuthConfig` and shape ``push``. Token-bearing values are never
    logged. ``parse_token_response`` is overridable for vendor field names.
    """

    settings: Settings
    http: httpx.AsyncClient

    def oauth_config(self) -> OAuthConfig:  # pragma: no cover - overridden
        raise NotImplementedError

    def parse_token_response(self, body: dict[str, object]) -> TokenSet:
        """Map a token endpoint JSON body to a :class:`TokenSet` (overridable)."""
        access = body.get("access_token")
        if not isinstance(access, str):
            raise ProviderError(
                PushErrorCode.AUTH,
                "token response missing access_token",
                response={"keys": sorted(body)},
            )
        refresh = body.get("refresh_token")
        expires_at: datetime | None = None
        expires_in = body.get("expires_in")
        if isinstance(expires_in, int | float):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        scope_raw = body.get("scope")
        scopes: tuple[str, ...] = ()
        if isinstance(scope_raw, str) and scope_raw:
            scopes = tuple(scope_raw.replace(",", " ").split())
        return TokenSet(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            expires_at=expires_at,
            scopes=scopes,
        )

    async def _token_request(self, data: dict[str, str]) -> TokenSet:
        config = self.oauth_config()
        if not config.configured:
            raise ProviderError(
                PushErrorCode.AUTH,
                "provider OAuth client is not configured",
            )
        form = {
            **data,
            "client_id": config.client_id or "",
            "client_secret": config.client_secret or "",
        }
        try:
            resp = await self.http.post(config.token_url, data=form)
        except httpx.HTTPError as exc:
            raise ProviderError(PushErrorCode.TRANSIENT, "token endpoint unreachable") from exc
        if resp.status_code >= 400:
            # Do not include the response body (may echo the secret/token).
            raise ProviderError(
                map_http_status_to_error_code(resp.status_code),
                f"token endpoint returned {resp.status_code}",
                response={"status_code": resp.status_code},
            )
        return self.parse_token_response(resp.json())

    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        token = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        # Many providers omit the refresh_token on refresh; reuse the old one so
        # the caller never blanks a still-valid refresh token.
        if token.refresh_token is None:
            token = TokenSet(
                access_token=token.access_token,
                refresh_token=refresh_token,
                expires_at=token.expires_at,
                scopes=token.scopes,
                provider_account=token.provider_account,
            )
        return token


# --- Registry ----------------------------------------------------------------

ProviderFactory = type[IntegrationProvider]
REGISTRY: dict[IntegrationProviderKind, ProviderFactory] = {}


def register_provider(factory: ProviderFactory) -> ProviderFactory:
    """Register a provider class under its ``kind`` (idempotent; overwrites)."""
    REGISTRY[factory.kind] = factory
    return factory


def get_provider(
    kind: IntegrationProviderKind, settings: Settings, http: httpx.AsyncClient
) -> IntegrationProvider:
    """Instantiate the registered provider for ``kind``, or raise ``KeyError``."""
    factory = REGISTRY[kind]
    return factory(settings, http)


def is_registered(kind: IntegrationProviderKind) -> bool:
    """Whether a provider class is registered for ``kind``."""
    return kind in REGISTRY


# --- TODO K2/K3/L1: real provider clients ------------------------------------
# K1 registers a generic OAuth2 stub so the framework + tests run end-to-end
# without vendor-specific code. K2 (Salesforce), K3 (HubSpot) and L1 (Slack)
# replace this with real authorize/token URLs and ``push`` mappings.


@register_provider
class _StubOAuthProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """A registered placeholder OAuth2 provider for the Salesforce slot (K1).

    Wired to the Salesforce ``kind`` so the framework has at least one OAuth
    provider to exercise. ``push`` raises ``NotImplementedError`` — the real
    object mapping is K2's job. The OAuth exchange/refresh come from the mixin
    and are fully testable with a mocked transport.

    # TODO K2: replace with the real Salesforce provider (instance_url discovery,
    #   Account/Lead upsert, custom-field mapping; doc 03 F11.1).
    """

    kind = IntegrationProviderKind.SALESFORCE

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url="https://login.salesforce.com/services/oauth2/authorize",
            token_url="https://login.salesforce.com/services/oauth2/token",
            scopes=("api", "refresh_token"),
            client_id=self.settings.salesforce_client_id,
            client_secret=self.settings.salesforce_client_secret,
        )

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        raise NotImplementedError("Salesforce push lands in K2")
