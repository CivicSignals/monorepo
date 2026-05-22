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

K2 lands the real :class:`SalesforceProvider` (object/field discovery + Opportunity
and custom-object push) in this slot; HubSpot (K3) and Slack (L1) register their
own providers analogously.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

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
    ``created`` is True when the push created a new record (vs updated an
    existing one for an idempotent re-push; the K4 upsert seam uses this).
    """

    external_id: str | None
    response: dict[str, object] = field(default_factory=dict)
    provider_response_id: str | None = None
    created: bool = True


# --- Object/field discovery (K2 field-mapping UI) ---------------------------


@dataclass(frozen=True, slots=True)
class FieldDescriptor:
    """One writable field on a provider object (K2 describe → mapping UI).

    Surfaced to the field-mapping UI so an admin can pick which provider field a
    signal/pipeline-item value maps onto. ``createable``/``updateable`` come from
    the provider describe; the UI only offers fields it can actually write.
    """

    name: str
    label: str
    type: str
    required: bool = False
    createable: bool = True
    updateable: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "createable": self.createable,
            "updateable": self.updateable,
        }


@dataclass(frozen=True, slots=True)
class ObjectDescriptor:
    """One pushable provider object (e.g. Salesforce ``Opportunity``) (K2).

    ``custom`` flags a tenant-defined custom object (``__c`` suffix in
    Salesforce), which the connector supports as a configurable push target.
    """

    name: str
    label: str
    custom: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "label": self.label, "custom": self.custom}


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

    #: Whether this provider supports object/field discovery (the field-mapping
    #: UI populates from :meth:`discover_objects` / :meth:`describe_object`).
    supports_discovery: bool = False

    async def discover_objects(
        self, *, access_token: str, provider_account: dict[str, object]
    ) -> list[ObjectDescriptor]:
        """List the provider objects this connection can push to (K2).

        Default: not supported (``supports_discovery = False``). Salesforce (K2)
        overrides this by querying the describe API; the field-mapping UI uses it
        to populate the object dropdown.
        """
        raise NotImplementedError("provider does not support object discovery")

    async def describe_object(
        self, *, access_token: str, provider_account: dict[str, object], object_name: str
    ) -> list[FieldDescriptor]:
        """List the writable fields on ``object_name`` (K2 field-mapping UI).

        Default: not supported. Salesforce overrides via the sobject describe API.
        """
        raise NotImplementedError("provider does not support field discovery")


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


# --- Salesforce provider (K2) ------------------------------------------------
# K2 replaces K1's stub with the real Salesforce connector: object/field
# discovery (sobjects describe), Opportunity + configurable custom-object push
# (create-or-update by external id for K4 idempotency), and scope-aware error
# mapping. The HTTP layer is the injected ``httpx.AsyncClient`` so tests mock the
# Salesforce REST transport (no live org).
# K3 (HubSpot) registers its own provider analogously (see below); L1 (Slack)
# registers a non-CRM provider for channel selection.

# Salesforce REST API version we pin requests to (path segment, e.g. /v60.0/).
SALESFORCE_API_VERSION = "v60.0"
# The default push target when a connection has no explicit target configured.
SALESFORCE_DEFAULT_OBJECT = "Opportunity"
# Salesforce field/object metadata used for describe → mapping UI is fetched
# from the org's instance_url; login.salesforce.com is only the OAuth host.
SALESFORCE_LOGIN_HOST = "https://login.salesforce.com"


def _salesforce_instance_url(provider_account: dict[str, object]) -> str:
    """The org's API base URL from the connection's stored ``instance_url``.

    Salesforce returns a per-org ``instance_url`` at token exchange; every REST
    call must target it (not the login host). Raises a ``not_found`` provider
    error when it is missing so the failure is actionable in the recovery UI.
    """
    instance = provider_account.get("instance_url")
    if not isinstance(instance, str) or not instance:
        raise ProviderError(
            PushErrorCode.NOT_FOUND,
            "connection has no Salesforce instance_url (reconnect required)",
        )
    return instance.rstrip("/")


@register_provider
class SalesforceProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """The Salesforce CRM connector (K2; doc 03 F11.1, doc 04 J3/F-2).

    OAuth2 authorization-code (the mixin handles exchange/refresh); ``push``
    creates or updates a Salesforce **Opportunity** — or a configurable **custom
    object** (``*__c``) — over the REST API, using the prior ``external_id`` to
    upsert idempotently (the K4 seam). Discovery lists the org's pushable objects
    and their writable fields so the field-mapping UI can populate. Vendor errors
    map onto the scope-aware :class:`PushErrorCode` taxonomy. Token material is
    never logged (threat-model §4.2).
    """

    kind = IntegrationProviderKind.SALESFORCE
    supports_discovery = True

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url=f"{SALESFORCE_LOGIN_HOST}/services/oauth2/authorize",
            token_url=f"{SALESFORCE_LOGIN_HOST}/services/oauth2/token",
            # ``api`` for the REST API, ``refresh_token`` for offline refresh.
            scopes=("api", "refresh_token"),
            client_id=self.settings.salesforce_client_id,
            client_secret=self.settings.salesforce_client_secret,
        )

    def parse_token_response(self, body: dict[str, object]) -> TokenSet:
        """Map the Salesforce token body, capturing the org ``instance_url``.

        Salesforce returns ``instance_url`` (the org's API host) and ``id`` (the
        identity URL) alongside the tokens; both are non-secret and persisted in
        ``provider_account`` so every REST call can target the right org. The
        token endpoint omits ``expires_in``, so we leave expiry unset and rely on
        a 401 → refresh on the next call.
        """
        tokens = super().parse_token_response(body)
        account: dict[str, object] = {}
        instance_url = body.get("instance_url")
        if isinstance(instance_url, str):
            account["instance_url"] = instance_url
        identity = body.get("id")
        if isinstance(identity, str):
            account["identity_url"] = identity
        if not account:
            return tokens
        return TokenSet(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
            provider_account=account,
        )

    # -- HTTP helpers --------------------------------------------------------

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        *,
        method: str,
        url: str,
        access_token: str,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Issue a Salesforce REST call, mapping transport/HTTP errors to typed.

        Network failures become ``transient``; an HTTP error status is mapped via
        :func:`map_http_status_to_error_code` (refined for Salesforce's field
        validation responses by :meth:`_raise_for_status`). Token material lives
        only in the request header, never in raised messages.
        """
        try:
            resp = await self.http.request(
                method,
                url,
                headers=self._auth_headers(access_token),
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(PushErrorCode.TRANSIENT, "Salesforce request failed") from exc
        return resp

    def _raise_for_status(self, resp: httpx.Response, *, context: str) -> None:
        """Raise a scope-aware :class:`ProviderError` for a non-2xx response.

        Salesforce returns an array of ``{errorCode, message, fields}`` objects;
        we surface the first message and the org request id (``Sforce-Limit-Info``
        / response id header) so the recovery UI can show the field error
        (doc 04 F-2: "Required field missing: Industry__c").
        """
        if resp.is_success:
            return
        code = map_http_status_to_error_code(resp.status_code)
        message = f"Salesforce {context} returned {resp.status_code}"
        detail: dict[str, object] | None = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, dict):
                detail = {k: first[k] for k in ("errorCode", "message", "fields") if k in first}
                msg = first.get("message")
                if isinstance(msg, str) and msg:
                    message = msg
        elif isinstance(body, dict):
            detail = body
            msg = body.get("message")
            if isinstance(msg, str) and msg:
                message = msg
        request_id = resp.headers.get("x-request-id") or resp.headers.get("apex-info")
        raise ProviderError(
            code,
            message,
            provider_response_id=request_id,
            response={"status_code": resp.status_code, "detail": detail},
        )

    # -- Discovery -----------------------------------------------------------

    async def discover_objects(
        self, *, access_token: str, provider_account: dict[str, object]
    ) -> list[ObjectDescriptor]:
        """List createable Salesforce objects (sobjects describe-global) (K2).

        Returns standard + custom objects the field-mapping UI offers as push
        targets; non-createable/system objects are filtered out.
        """
        base = _salesforce_instance_url(provider_account)
        url = f"{base}/services/data/{SALESFORCE_API_VERSION}/sobjects"
        resp = await self._request(method="GET", url=url, access_token=access_token)
        self._raise_for_status(resp, context="describe-global")
        body = resp.json()
        sobjects = body.get("sobjects") if isinstance(body, dict) else None
        objects: list[ObjectDescriptor] = []
        if isinstance(sobjects, list):
            for entry in sobjects:
                if not isinstance(entry, dict):
                    continue
                if not entry.get("createable"):
                    continue
                name = entry.get("name")
                if not isinstance(name, str):
                    continue
                label = entry.get("label")
                objects.append(
                    ObjectDescriptor(
                        name=name,
                        label=label if isinstance(label, str) else name,
                        custom=bool(entry.get("custom")),
                    )
                )
        return objects

    async def describe_object(
        self, *, access_token: str, provider_account: dict[str, object], object_name: str
    ) -> list[FieldDescriptor]:
        """List writable fields on ``object_name`` (sobject describe) (K2).

        Returns the createable/updateable fields so the mapping UI only offers
        fields it can actually write; ``required`` (nillable=false + no default)
        is surfaced so the UI can warn before a push fails validation.
        """
        base = _salesforce_instance_url(provider_account)
        safe_name = object_name.replace("/", "")
        url = f"{base}/services/data/{SALESFORCE_API_VERSION}/sobjects/{safe_name}/describe"
        resp = await self._request(method="GET", url=url, access_token=access_token)
        self._raise_for_status(resp, context=f"describe {object_name}")
        body = resp.json()
        raw_fields = body.get("fields") if isinstance(body, dict) else None
        fields: list[FieldDescriptor] = []
        if isinstance(raw_fields, list):
            for entry in raw_fields:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str):
                    continue
                createable = bool(entry.get("createable"))
                updateable = bool(entry.get("updateable"))
                if not createable and not updateable:
                    continue
                label = entry.get("label")
                required = (
                    not entry.get("nillable", True)
                    and not entry.get("defaultedOnCreate", False)
                    and createable
                )
                fields.append(
                    FieldDescriptor(
                        name=name,
                        label=label if isinstance(label, str) else name,
                        type=str(entry.get("type", "string")),
                        required=required,
                        createable=createable,
                        updateable=updateable,
                    )
                )
        return fields

    # -- Push (create-or-update Opportunity / custom object) -----------------

    def _object_name(self, target: str) -> str:
        """Resolve the Salesforce sobject name from a push target.

        ``target`` is the provider-qualified target (e.g. ``salesforce.opportunity``
        or ``salesforce.Custom_Deal__c``). The bare object name maps to the
        Salesforce sobject (Opportunity by default; custom objects keep their
        ``__c`` casing).
        """
        _, _, obj = target.partition(".")
        obj = obj.strip()
        if not obj:
            return SALESFORCE_DEFAULT_OBJECT
        # Canonicalise the well-known standard object regardless of case.
        if obj.lower() == "opportunity":
            return SALESFORCE_DEFAULT_OBJECT
        return obj

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        """Create or update a Salesforce object from the mapped payload (K2).

        ``request.payload`` is the already-mapped, secret-free body of Salesforce
        field → value pairs (the field-mapping service shapes it). When
        ``request.external_id`` is present the push is an idempotent PATCH update
        (the K4 seam); otherwise it is a POST create. The new/updated record id is
        returned as ``external_id``. Vendor errors map to scope-aware codes.
        """
        provider_account = cast(
            "dict[str, object]", request.payload.pop("__provider_account__", {})
        )
        base = _salesforce_instance_url(
            provider_account if isinstance(provider_account, dict) else {}
        )
        object_name = self._object_name(request.target)
        sobject_base = f"{base}/services/data/{SALESFORCE_API_VERSION}/sobjects/{object_name}"

        # Strip control keys; only field/value pairs go to Salesforce.
        body = {k: v for k, v in request.payload.items() if not k.startswith("__")}

        if request.external_id:
            # Idempotent update of the previously-created record (K4 upsert seam).
            url = f"{sobject_base}/{request.external_id}"
            resp = await self._request(
                method="PATCH", url=url, access_token=access_token, json_body=body
            )
            self._raise_for_status(resp, context=f"update {object_name}")
            request_id = resp.headers.get("x-request-id")
            return PushResult(
                external_id=request.external_id,
                response={"id": request.external_id, "updated": True},
                provider_response_id=request_id,
                created=False,
            )

        # Create a new record.
        resp = await self._request(
            method="POST", url=sobject_base, access_token=access_token, json_body=body
        )
        self._raise_for_status(resp, context=f"create {object_name}")
        result_body = resp.json() if resp.content else {}
        new_id = result_body.get("id") if isinstance(result_body, dict) else None
        if not isinstance(new_id, str):
            raise ProviderError(
                PushErrorCode.UNKNOWN,
                "Salesforce create returned no record id",
                response={"keys": sorted(result_body) if isinstance(result_body, dict) else []},
            )
        return PushResult(
            external_id=new_id,
            response={"id": new_id, "success": bool(result_body.get("success", True))},
            provider_response_id=resp.headers.get("x-request-id"),
            created=True,
        )


# --- HubSpot provider (K3) ----------------------------------------------------
# K3 mirrors the Salesforce connector (K2) for HubSpot: object discovery (CRM v3
# schemas), writable-property discovery (CRM v3 properties), and create-or-update
# push of a **Deal** — or a configurable **custom object** — over the CRM v3 REST
# API, using the prior ``external_id`` to upsert idempotently (the K4 seam).
# HubSpot is standard OAuth2 authorization-code (the mixin handles exchange /
# refresh). Unlike Salesforce there is no per-org instance host: every REST call
# targets the fixed ``api.hubapi.com`` API base, so no ``instance_url`` is needed.
# The HTTP layer is the injected ``httpx.AsyncClient`` so tests mock the HubSpot
# REST transport (no live portal). Token material is never logged (§4.2).

# HubSpot OAuth + CRM v3 hosts.
HUBSPOT_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE = "https://api.hubapi.com"
# CRM REST version segment we pin requests to (e.g. /crm/v3/objects/deals).
HUBSPOT_CRM_VERSION = "v3"
# The default push target when a connection has no explicit target configured.
# HubSpot's Deal object's plural API name is ``deals`` (used in the object path).
HUBSPOT_DEFAULT_OBJECT = "deals"

# Default scopes: CRM read/write for objects + schemas. ``oauth`` is implicitly
# granted. Discovery (schemas/properties) is covered by ``crm.objects.*`` +
# ``crm.schemas.*`` read scopes; a deal create/update needs the deals write scope.
HUBSPOT_DEFAULT_SCOPES: tuple[str, ...] = (
    "crm.objects.deals.read",
    "crm.objects.deals.write",
    "crm.objects.custom.read",
    "crm.objects.custom.write",
    "crm.schemas.deals.read",
    "crm.schemas.custom.read",
)

# Standard HubSpot CRM object type names whose schemas the discovery endpoint
# does not return (it only lists custom objects + a subset), so we always offer
# the well-known standard objects the connector can push to.
HUBSPOT_STANDARD_OBJECTS: tuple[tuple[str, str], ...] = (
    ("deals", "Deal"),
    ("contacts", "Contact"),
    ("companies", "Company"),
    ("tickets", "Ticket"),
)


@register_provider
class HubSpotProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """The HubSpot CRM connector (K3; mirrors K2 Salesforce, doc 03 F12).

    OAuth2 authorization-code (the mixin handles exchange/refresh); ``push``
    creates or updates a HubSpot **Deal** — or a configurable **custom object** —
    over the CRM v3 REST API, using the prior ``external_id`` to upsert
    idempotently (the K4 seam). Discovery lists the portal's pushable objects
    (standard + custom) and their writable properties so the field-mapping UI can
    populate. Vendor errors map onto the scope-aware :class:`PushErrorCode`
    taxonomy. Token material is never logged (threat-model §4.2).

    Unlike Salesforce there is no per-org ``instance_url``: every REST call
    targets the fixed ``api.hubapi.com`` host. The non-secret portal id (``hub_id``)
    is captured in ``provider_account`` for the UI to label the connection.

    Config-gated: ``HUBSPOT_CLIENT_ID`` / ``HUBSPOT_CLIENT_SECRET`` (the route
    returns a clear 422 when unset, like the other providers).
    """

    kind = IntegrationProviderKind.HUBSPOT
    supports_discovery = True

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url=HUBSPOT_AUTHORIZE_URL,
            token_url=HUBSPOT_TOKEN_URL,
            scopes=HUBSPOT_DEFAULT_SCOPES,
            client_id=self.settings.hubspot_client_id,
            client_secret=self.settings.hubspot_client_secret,
        )

    def parse_token_response(self, body: dict[str, object]) -> TokenSet:
        """Map the HubSpot token body, capturing the non-secret portal id.

        HubSpot returns ``access_token`` / ``refresh_token`` / ``expires_in`` (and
        a ``hub_id`` on some responses); the base mixin handles the tokens +
        expiry. The portal id (``hub_id``) is non-secret and persisted in
        ``provider_account`` for the UI; the API base is fixed so it is not
        required to route REST calls (unlike Salesforce's ``instance_url``).
        """
        tokens = super().parse_token_response(body)
        account: dict[str, object] = {}
        hub_id = body.get("hub_id")
        if isinstance(hub_id, str | int):
            account["hub_id"] = str(hub_id)
        hub_domain = body.get("hub_domain")
        if isinstance(hub_domain, str):
            account["hub_domain"] = hub_domain
        if not account:
            return tokens
        return TokenSet(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
            provider_account=account,
        )

    # -- HTTP helpers --------------------------------------------------------

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        *,
        method: str,
        url: str,
        access_token: str,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Issue a HubSpot CRM v3 REST call, mapping transport errors to typed.

        Network failures become ``transient``; an HTTP error status is mapped via
        :meth:`_raise_for_status`. Token material lives only in the request
        header, never in raised messages.
        """
        try:
            resp = await self.http.request(
                method,
                url,
                headers=self._auth_headers(access_token),
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(PushErrorCode.TRANSIENT, "HubSpot request failed") from exc
        return resp

    def _raise_for_status(self, resp: httpx.Response, *, context: str) -> None:
        """Raise a scope-aware :class:`ProviderError` for a non-2xx response.

        HubSpot returns a JSON error envelope ``{message, category, correlationId,
        errors:[…]}``; we surface ``message`` and the ``correlationId`` (the
        portal-side request trace shown in the K5 recovery UI). ``category`` such
        as ``VALIDATION_ERROR`` refines the HTTP-status → typed-error mapping.
        """
        if resp.is_success:
            return
        code = map_http_status_to_error_code(resp.status_code)
        message = f"HubSpot {context} returned {resp.status_code}"
        detail: dict[str, object] | None = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = {k: body[k] for k in ("message", "category", "errors") if k in body}
            msg = body.get("message")
            if isinstance(msg, str) and msg:
                message = msg
            # A VALIDATION_ERROR category is a non-retryable payload problem even
            # when HubSpot returns it with a 400/409 (already ``validation``).
            if body.get("category") == "VALIDATION_ERROR":
                code = PushErrorCode.VALIDATION
        correlation_id = resp.headers.get("x-hubspot-correlation-id")
        if correlation_id is None and isinstance(body, dict):
            cid = body.get("correlationId")
            if isinstance(cid, str):
                correlation_id = cid
        raise ProviderError(
            code,
            message,
            provider_response_id=correlation_id,
            response={"status_code": resp.status_code, "detail": detail},
        )

    # -- Discovery -----------------------------------------------------------

    async def discover_objects(
        self, *, access_token: str, provider_account: dict[str, object]
    ) -> list[ObjectDescriptor]:
        """List the HubSpot objects this connection can push to (K3).

        Always offers the well-known standard objects (Deal/Contact/Company/
        Ticket) the connector supports, then appends the portal's custom objects
        from the CRM v3 schemas API. The field-mapping UI uses this to populate
        the object dropdown.
        """
        objects: list[ObjectDescriptor] = [
            ObjectDescriptor(name=name, label=label, custom=False)
            for name, label in HUBSPOT_STANDARD_OBJECTS
        ]
        url = f"{HUBSPOT_API_BASE}/crm/{HUBSPOT_CRM_VERSION}/schemas"
        resp = await self._request(method="GET", url=url, access_token=access_token)
        self._raise_for_status(resp, context="schemas")
        body = resp.json()
        results = body.get("results") if isinstance(body, dict) else None
        seen = {o.name for o in objects}
        if isinstance(results, list):
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                # Custom objects expose ``name`` (the object type's API name) and
                # ``labels.plural`` / ``labels.singular`` for display.
                name = entry.get("fullyQualifiedName") or entry.get("name")
                if not isinstance(name, str) or name in seen:
                    continue
                labels = entry.get("labels")
                label = name
                if isinstance(labels, dict):
                    plural = labels.get("plural") or labels.get("singular")
                    if isinstance(plural, str) and plural:
                        label = plural
                objects.append(ObjectDescriptor(name=name, label=label, custom=True))
                seen.add(name)
        return objects

    async def describe_object(
        self, *, access_token: str, provider_account: dict[str, object], object_name: str
    ) -> list[FieldDescriptor]:
        """List the writable properties on ``object_name`` (CRM v3 properties) (K3).

        Returns the non-read-only, non-calculated properties so the mapping UI only
        offers properties it can actually write; HubSpot does not flag a property
        ``createable``/``updateable`` separately (a writable property is writable on
        both), so both flags mirror the writable check. ``required`` reflects the
        HubSpot ``required`` flag (rare for deal properties).
        """
        obj = object_name.replace("/", "")
        url = f"{HUBSPOT_API_BASE}/crm/{HUBSPOT_CRM_VERSION}/properties/{obj}"
        resp = await self._request(method="GET", url=url, access_token=access_token)
        self._raise_for_status(resp, context=f"properties {object_name}")
        body = resp.json()
        results = body.get("results") if isinstance(body, dict) else None
        fields: list[FieldDescriptor] = []
        if isinstance(results, list):
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str):
                    continue
                # Skip properties we can never write: read-only, HubSpot-calculated,
                # or system fields (``hs_`` calculated). ``modificationMetadata``
                # carries the authoritative read-only flag when present.
                if entry.get("calculated") or entry.get("hidden"):
                    continue
                mod = entry.get("modificationMetadata")
                if isinstance(mod, dict) and mod.get("readOnlyValue") is True:
                    continue
                label = entry.get("label")
                fields.append(
                    FieldDescriptor(
                        name=name,
                        label=label if isinstance(label, str) else name,
                        type=str(entry.get("type", "string")),
                        required=bool(entry.get("required", False)),
                        createable=True,
                        updateable=True,
                    )
                )
        return fields

    # -- Push (create-or-update Deal / custom object) ------------------------

    def _object_name(self, target: str) -> str:
        """Resolve the HubSpot object API name from a push target.

        ``target`` is the provider-qualified target (e.g. ``hubspot.deals`` or
        ``hubspot.deal``, or a custom object's fully-qualified name). The bare
        object name maps to the CRM object path segment (``deals`` by default);
        the canonical ``deal``/``deals`` alias resolves to ``deals``.
        """
        _, _, obj = target.partition(".")
        obj = obj.strip()
        if not obj:
            return HUBSPOT_DEFAULT_OBJECT
        if obj.lower() in ("deal", "deals"):
            return HUBSPOT_DEFAULT_OBJECT
        return obj

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        """Create or update a HubSpot object from the mapped payload (K3).

        ``request.payload`` is the already-mapped, secret-free body of HubSpot
        property → value pairs (the field-mapping service shapes it). HubSpot wraps
        property values in a ``{"properties": {...}}` envelope. When
        ``request.external_id`` is present the push is an idempotent PATCH update
        (the K4 seam); otherwise it is a POST create. The new/updated record id is
        returned as ``external_id``. Vendor errors map to scope-aware codes.
        """
        object_name = self._object_name(request.target)
        objects_base = f"{HUBSPOT_API_BASE}/crm/{HUBSPOT_CRM_VERSION}/objects/{object_name}"

        # Strip control keys (e.g. ``__provider_account__`` injected by the push
        # runner — HubSpot needs no instance host); only field/value pairs go up,
        # wrapped in HubSpot's ``properties`` envelope. Values are stringified by
        # HubSpot itself; we pass them through as-is.
        properties = {k: v for k, v in request.payload.items() if not k.startswith("__")}
        json_body: dict[str, object] = {"properties": properties}

        if request.external_id:
            # Idempotent update of the previously-created record (K4 upsert seam).
            url = f"{objects_base}/{request.external_id}"
            resp = await self._request(
                method="PATCH", url=url, access_token=access_token, json_body=json_body
            )
            self._raise_for_status(resp, context=f"update {object_name}")
            correlation_id = resp.headers.get("x-hubspot-correlation-id")
            return PushResult(
                external_id=request.external_id,
                response={"id": request.external_id, "updated": True},
                provider_response_id=correlation_id,
                created=False,
            )

        # Create a new record.
        resp = await self._request(
            method="POST", url=objects_base, access_token=access_token, json_body=json_body
        )
        self._raise_for_status(resp, context=f"create {object_name}")
        result_body = resp.json() if resp.content else {}
        new_id = result_body.get("id") if isinstance(result_body, dict) else None
        if not isinstance(new_id, str):
            raise ProviderError(
                PushErrorCode.UNKNOWN,
                "HubSpot create returned no record id",
                response={"keys": sorted(result_body) if isinstance(result_body, dict) else []},
            )
        return PushResult(
            external_id=new_id,
            response={"id": new_id},
            provider_response_id=resp.headers.get("x-hubspot-correlation-id"),
            created=True,
        )


# --- Slack provider (L1) -------------------------------------------------------
# Slack OAuth v2 (oauth.v2.access), bot token, channel listing.  The HTTP layer
# is the injected httpx.AsyncClient (no live Slack calls in tests).  Token
# material is never logged (threat-model §4.2).

SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"

# Default scopes: bot token needs channels:read (list channels) + chat:write
# (send messages — consumed by L2).  chat:write.public is added so the bot can
# post to channels it hasn't been invited to.
SLACK_DEFAULT_SCOPES: tuple[str, ...] = (
    "channels:read",
    "chat:write",
    "chat:write.public",
)


@dataclass(frozen=True, slots=True)
class SlackChannel:
    """A Slack channel returned by conversations.list (L1)."""

    id: str
    name: str
    is_private: bool = False
    is_member: bool = False


@register_provider
class SlackProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """The Slack workspace-install connector (L1).

    OAuth v2 install flow (``oauth.v2.access``): the bot token is returned
    in ``access_token`` (no user token for this flow); the team id/name come
    from the ``team`` block and are stored in ``provider_account`` (non-secret).
    Slack does not issue a refresh_token for bot tokens (they don't expire), so
    ``refresh`` raises ``NotImplementedError`` in the bot-token path.

    Channel listing via ``conversations.list`` is exposed via a dedicated
    ``list_channels`` method (called by the L1 routes) rather than the K2
    discovery interface (which is object/field-level, not channel-level).

    ``push`` is a L2 seam: the L1 task is connection + channel selection only.
    L2 lands the formatted message send; ``push`` raises ``NotImplementedError``
    here to make accidental routing obvious.

    Config-gated: ``SLACK_CLIENT_ID`` / ``SLACK_CLIENT_SECRET`` (no-op /
    ``provider_not_configured`` when unset, like other providers).
    """

    kind = IntegrationProviderKind.SLACK
    is_oauth: bool = True

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url=SLACK_OAUTH_AUTHORIZE_URL,
            token_url=SLACK_OAUTH_TOKEN_URL,
            scopes=SLACK_DEFAULT_SCOPES,
            client_id=self.settings.slack_client_id,
            client_secret=self.settings.slack_client_secret,
        )

    def parse_token_response(self, body: dict[str, object]) -> TokenSet:
        """Map a Slack oauth.v2.access response to a TokenSet (L1).

        Slack v2 returns ``ok: True`` on success; the bot access token lives at
        ``access_token``; team metadata lives in ``team``.  Scopes are in
        ``scope`` (comma-separated) on the bot-token body or on the
        ``authed_user`` sub-object (we capture the bot scopes).  No refresh_token
        is issued (bot tokens don't expire).
        """
        ok = body.get("ok")
        if ok is not True:
            error = body.get("error", "unknown_error")
            raise ProviderError(
                PushErrorCode.AUTH,
                f"Slack token exchange failed: {error}",
                response={"error": error},
            )
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise ProviderError(
                PushErrorCode.AUTH,
                "Slack token response missing access_token",
                response={"keys": sorted(body)},
            )
        scope_raw = body.get("scope", "")
        scopes: tuple[str, ...] = ()
        if isinstance(scope_raw, str) and scope_raw:
            scopes = tuple(scope_raw.split(","))

        account: dict[str, object] = {}
        team = body.get("team")
        if isinstance(team, dict):
            if isinstance(team.get("id"), str):
                account["team_id"] = team["id"]
            if isinstance(team.get("name"), str):
                account["team_name"] = team["name"]
        bot_user_id = body.get("bot_user_id")
        if isinstance(bot_user_id, str):
            account["bot_user_id"] = bot_user_id
        # app_id is non-secret and useful for the UI.
        app_id = body.get("app_id")
        if isinstance(app_id, str):
            account["app_id"] = app_id

        return TokenSet(
            access_token=access,
            refresh_token=None,  # bot tokens don't expire
            expires_at=None,
            scopes=scopes,
            provider_account=account,
        )

    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        """Exchange an authorization code via oauth.v2.access (L1).

        Slack v2 uses HTTP Basic auth (client_id:client_secret) rather than
        including credentials in the request body; the mixin's ``_token_request``
        is not used here (it injects credentials into the body, which Slack also
        accepts, but Basic auth is the recommended form).
        """
        config = self.oauth_config()
        if not config.configured:
            raise ProviderError(
                PushErrorCode.AUTH,
                "Slack OAuth client is not configured (SLACK_CLIENT_ID/SECRET missing)",
            )
        data = {
            "code": code,
            "redirect_uri": redirect_uri,
        }
        try:
            resp = await self.http.post(
                config.token_url,
                data=data,
                auth=(config.client_id or "", config.client_secret or ""),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(
                PushErrorCode.TRANSIENT, "Slack token endpoint unreachable"
            ) from exc
        if resp.status_code >= 400:
            raise ProviderError(
                map_http_status_to_error_code(resp.status_code),
                f"Slack token endpoint returned {resp.status_code}",
                response={"status_code": resp.status_code},
            )
        return self.parse_token_response(resp.json())

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        """Bot tokens do not expire; this is a no-op seam (L1).

        Slack workspace app bot tokens are long-lived and have no refresh flow.
        If a token is revoked the admin must reconnect (the status transitions to
        ``needs_reauth`` when a Slack API call returns ``invalid_auth``).
        """
        raise ProviderError(
            PushErrorCode.AUTH,
            "Slack bot tokens do not support refresh; reconnect required",
        )

    async def list_channels(self, *, access_token: str) -> list[SlackChannel]:
        """List joinable public channels via conversations.list (L1).

        Returns channels the bot can post to.  Paginates automatically (cursor-
        based).  Rate-limited by Slack Tier 2 (20 req/min); we fetch up to 5
        pages of 200 channels (1 000 channels max) which covers typical orgs.
        Raises :class:`ProviderError` on Slack API failure.
        """
        channels: list[SlackChannel] = []
        cursor: str | None = None
        max_pages = 5
        for _ in range(max_pages):
            params: dict[str, str] = {
                "exclude_archived": "true",
                "types": "public_channel",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                resp = await self.http.get(
                    SLACK_CONVERSATIONS_LIST_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    PushErrorCode.TRANSIENT, "Slack conversations.list unreachable"
                ) from exc
            if resp.status_code >= 400:
                raise ProviderError(
                    map_http_status_to_error_code(resp.status_code),
                    f"Slack conversations.list returned {resp.status_code}",
                )
            body = resp.json()
            if not body.get("ok"):
                error = body.get("error", "unknown_error")
                if error in ("invalid_auth", "token_revoked", "account_inactive"):
                    raise ProviderError(PushErrorCode.AUTH, f"Slack auth error: {error}")
                raise ProviderError(PushErrorCode.UNKNOWN, f"conversations.list failed: {error}")
            for ch in body.get("channels", []):
                if not isinstance(ch, dict):
                    continue
                ch_id = ch.get("id")
                ch_name = ch.get("name")
                if not isinstance(ch_id, str) or not isinstance(ch_name, str):
                    continue
                channels.append(
                    SlackChannel(
                        id=ch_id,
                        name=ch_name,
                        is_private=bool(ch.get("is_private")),
                        is_member=bool(ch.get("is_member")),
                    )
                )
            next_cursor = (body.get("response_metadata") or {}).get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return channels

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        """L2 seam: send a formatted Slack message (not yet implemented in L1).

        L1 is connection + channel selection only; L2 lands the message format
        and the actual ``chat.postMessage`` call.  Raising here makes accidental
        routing obvious without silently dropping the push.

        # TODO L2: implement Slack push (chat.postMessage to the selected channel).
        """
        raise NotImplementedError(
            "Slack push (chat.postMessage) is implemented in L2. "
            "L1 covers connection + channel selection only."
        )


# --- L3: Webhook provider (non-OAuth, secret-based) ---------------------------
# Webhook subscriptions are managed directly via the /webhooks CRUD endpoints;
# the provider registration here lets the integrations framework recognise
# ``provider=webhook`` without raising ``ProviderNotRegisteredError``. Delivery
# is handled by the dedicated webhook delivery service (services.py), not by this
# provider's ``push`` method, so ``push`` raises ``NotImplementedError`` to make
# accidental routing obvious. The OAuth methods are not used; ``is_oauth = False``
# signals to ``start_oauth`` that the consent flow does not apply.


@register_provider
class WebhookProvider(IntegrationProvider):
    """K1 provider entry for the generic outbound webhook (L3).

    Registered so the framework's provider-lookup does not raise for
    ``kind=WEBHOOK``. Delivery is handled by the webhook-specific service layer
    (``services.deliver_event_to_subscribers`` / ``execute_webhook_delivery``);
    this class exists only to satisfy the registry contract.
    """

    kind = IntegrationProviderKind.WEBHOOK
    is_oauth: bool = False

    def oauth_config(self) -> OAuthConfig:
        raise NotImplementedError("webhook provider is not OAuth-based (L3)")

    async def exchange_code(self, *, code: str, redirect_uri: str) -> TokenSet:
        raise NotImplementedError("webhook provider is not OAuth-based (L3)")

    async def refresh(self, *, refresh_token: str) -> TokenSet:
        raise NotImplementedError("webhook provider is not OAuth-based (L3)")

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        raise NotImplementedError(
            "webhook delivery goes through services.execute_webhook_delivery (L3)"
        )
