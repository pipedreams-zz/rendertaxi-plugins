"""Gerätelogin nach RFC 8628 (``docs/api/plugin-api-v1.md``, Abschnitt 5; ADR 0022).

Kein Passwortfeld, kein eingebetteter Browser: das Plugin zeigt Code und
Adresse, öffnet auf Wunsch den Standardbrowser und fragt im vorgegebenen
Abstand ab. ``slow_down`` erhöht den Abstand dauerhaft, Netzwerkfehler
verdoppeln ihn.

Kein Hostmodul; läuft im Hintergrundfaden des Plugins.
"""

from __future__ import annotations

import platform
import threading
import time

from . import current
from . import manifest as mf
from .log import log
from .store import CredentialStore
from .transport import ApiClient, ApiError, Cancelled, Unauthorized

MAX_INTERVAL_SECONDS = 60


def machine() -> dict:
    """``os`` und ``architecture`` — kein Rechner-, kein Benutzername."""
    system = platform.system()
    os_key = {"Darwin": "macos", "Windows": "windows"}.get(system, "linux")
    arch = platform.machine().lower()
    architecture = "arm64" if arch in ("arm64", "aarch64") else "x64"
    entry = {"os": os_key, "architecture": architecture}
    version = {"macos": platform.mac_ver()[0], "windows": platform.version()}.get(os_key, "")
    safe = "".join(ch for ch in version if ch.isalnum() or ch in "._ -")[:32]
    if safe:
        entry["osVersion"] = safe
    return entry


def device(store: CredentialStore, server: str, host_version: str, plugin_version: str,
           display_name: str = "") -> dict:
    """Die Gerätebeschreibung; ``deviceId`` einmal je Installation und Server erzeugt."""
    device_id = store.device_id(server)
    if device_id is None:
        device_id = mf.uuid_v7()
        store.remember_device(server, device_id)
    entry = {
        "deviceId": device_id,
        "hostKey": current().key,
        "hostVersion": host_version,
        "pluginVersion": plugin_version,
        "os": machine()["os"],
        "architecture": machine()["architecture"],
    }
    name = display_name.strip()[:64]
    if name:
        entry["displayName"] = name
    return entry


class DeviceLogin:
    """Ein Anmeldevorgang. ``on_code`` bekommt Code und Adressen zur Anzeige."""

    def __init__(self, api: ApiClient, store: CredentialStore, device_description: dict,
                 cancel: threading.Event | None = None, sleep=None):
        self.api = api
        self.store = store
        self.device = device_description
        self.cancel = cancel or threading.Event()
        self.sleep = sleep

    def _wait(self, seconds: float) -> None:
        """Warten, bis abgefragt werden darf — ``Abbrechen`` beendet das Warten sofort."""
        if self.sleep is None:
            if self.cancel.wait(seconds):
                raise Cancelled()
            return
        self.sleep(seconds)  # Tests: ohne echte Wartezeit
        if self.cancel.is_set():
            raise Cancelled()

    def run(self, on_code) -> dict:
        codes = self.api.device_authorization(self.device)
        on_code({
            "userCode": codes["user_code"],
            "verificationUri": codes["verification_uri"],
            "verificationUriComplete": codes.get("verification_uri_complete") or codes["verification_uri"],
            "expiresAt": time.time() + int(codes.get("expires_in", 600)),
        })
        interval = max(int(codes.get("interval", 5)), 1)
        deadline = time.time() + int(codes.get("expires_in", 600))
        while True:
            self._wait(interval)
            if time.time() > deadline + interval:
                raise ApiError("Der Code ist abgelaufen. Bitte erneut verbinden.", code="expired_token")
            try:
                token = self.api.device_token(codes["device_code"])
            except ApiError as error:
                if error.code == "network":
                    interval = min(interval * 2, MAX_INTERVAL_SECONDS)
                    continue
                reason = error.reason or error.code
                if reason == "authorization_pending":
                    continue
                if reason == "slow_down":
                    interval = int(error.details.get("interval") or interval + 5)
                    continue
                if reason == "rate_limited":
                    interval = min(interval * 2, MAX_INTERVAL_SECONDS)
                    continue
                text = {
                    "access_denied": "Die Anmeldung wurde im Browser abgelehnt.",
                    "expired_token": "Der Code ist abgelaufen. Bitte erneut verbinden.",
                    "invalid_grant": "Der Code gilt nicht mehr. Bitte erneut verbinden.",
                }.get(reason, str(error))
                raise ApiError(text, status=error.status, code=error.code, reason=reason) from None
            access = token.get("access_token")
            if not access:
                raise ApiError("Der Server hat kein Token geliefert.", code="invalid_response")
            self.store.store_token(self.api.server_url, access)
            self.api.token = access
            log("Anmeldung: Token erhalten und abgelegt (wird nicht ausgegeben).")
            return identity(self.api)


def identity(api: ApiClient) -> dict:
    """``GET /plugin/me`` — für „Angemeldet als …"; wirft ``Unauthorized`` bei ``401``."""
    me = api.me()
    user = me.get("user") or {}
    organization = me.get("organization") or {}
    return {
        "user": user.get("displayName") or user.get("email") or "",
        "email": user.get("email") or "",
        "organization": organization.get("name") or "",
        "expiresAt": (me.get("token") or {}).get("expiresAt"),
    }


def sign_out(api: ApiClient, store: CredentialStore) -> str | None:
    """Beim Server widerrufen **und** lokal löschen.

    Das lokale Token wird auch gelöscht, wenn der Server nicht erreichbar ist;
    dann nennt die Rückgabe den Weg über „Verbundene Geräte" im Web.
    """
    token = store.token(api.server_url)
    warning = None
    if token:
        try:
            api.revoke(token)
        except (ApiError, Unauthorized):
            warning = ("Der Server hat den Widerruf nicht bestätigt. Das Gerät lässt sich "
                       "in der Webanwendung unter „Verbundene Geräte“ abmelden.")
    store.forget_token(api.server_url)
    api.token = None
    log("Abgemeldet; Token lokal gelöscht.")
    return warning
