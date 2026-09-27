"""Capture-Manifest 1.1.0 — erzeugen, hashen und **vor** dem Hochladen prüfen.

Dieses Modul kennt ``bpy`` nicht. Es ist die Python-Fassung dessen, was der
Referenzclient (``integrations/_shared/tools/capture-client.mjs``) und das
Archicad-Add-on (``core/src/CaptureManifest.cpp``) tun:

* ``content_hash`` ist zeichengleich zu ``canonical-hash.mjs`` — Assetzeile
  ``asset ⇥ role ⇥ path ⇥ sha256`` mit längenpräfigierten Feldern, sortiert
  nach UTF-8-Bytes, je Zeile ein Zeilenvorschub.
* ``SchemaRegistry`` ist eine Übertragung von ``json-schema.mjs``: dieselbe,
  bewusst begrenzte Teilmenge von JSON Schema 2020-12, ebenso streng — ein
  unbekanntes Schlüsselwort ist ein Fehler, kein stilles Ignorieren.
* Die Schemas liegen als Kopie unter ``schema/``; ``tests/contract/
  blender-addon.test.ts`` hält sie bytegleich zu ``integrations/_shared``.

Ein Manifest, das hier nicht validiert, wird nicht hochgeladen
(``docs/api/plugin-api-v1.md``, Abschnitt 7.1, Schritt 4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field

CONTRACT = "rendertaxi.plugin.capture-manifest"
CONTRACT_VERSION = "1.1.0"
CLIENT_ID = "ai.rendertaxi.plugin.blender"
HOST_KEY = "blender"

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(ADDON_DIR, "schema")
SCHEMA_FILES = (
    "capture-manifest.schema.json",
    "common.schema.json",
    "host-capabilities.schema.json",
)


def plugin_version() -> str:
    """Die eine Quelle der Pluginversion: ``version`` in ``blender_manifest.toml``."""
    import tomllib

    with open(os.path.join(ADDON_DIR, "blender_manifest.toml"), "rb") as handle:
        return str(tomllib.load(handle)["version"])


# --------------------------------------------------------------------------
# Kennungen
# --------------------------------------------------------------------------


def uuid_v7() -> str:
    """Eine UUIDv7 — 48 Bit Millisekunden, Version 7, Variante 10, Zufall."""
    raw = bytearray(secrets.token_bytes(16))
    millis = int(time.time() * 1000)
    raw[0:6] = millis.to_bytes(6, "big")
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    text = raw.hex()
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


def timestamp_utc(seconds: float | None = None) -> str:
    """UTC nach RFC 3339 mit Millisekunden und ``Z``, wie der Vertrag sie verlangt."""
    value = time.time() if seconds is None else seconds
    whole = time.gmtime(value)
    millis = int((value - int(value)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", whole) + f".{millis:03d}Z"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# --------------------------------------------------------------------------
# contentHash
# --------------------------------------------------------------------------


def _enc(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value))
    return f"{len(normalized.encode('utf-8'))}:{normalized}"


def content_hash(assets: list[dict]) -> str:
    """SHA-256 über die sortierten Assetzeilen der vorhandenen Assets."""
    lines = [
        "\t".join((_enc("asset"), _enc(a["role"]), _enc(a["path"]), _enc(a["sha256"])))
        for a in assets
        if a.get("status") == "present"
    ]
    lines.sort(key=lambda line: line.encode("utf-8"))
    return sha256_hex("".join(f"{line}\n" for line in lines).encode("utf-8"))


# --------------------------------------------------------------------------
# JSON-Schema-Teilmenge (Übertragung von json-schema.mjs)
# --------------------------------------------------------------------------

_IGNORED = {"$schema", "$id", "$comment", "$defs", "title", "description", "examples"}
_SUPPORTED = {
    "$ref", "type", "enum", "const", "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "allOf", "anyOf",
    "oneOf", "not", "if", "then", "else",
}


def _type_of(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _matches_type(value, expected: str) -> bool:
    actual = _type_of(value)
    if expected == "integer":
        return actual == "number" and float(value).is_integer()
    if expected == "number":
        return actual == "number" and value == value and abs(value) != float("inf")
    return actual == expected


def _deep_equal(a, b) -> bool:
    if _type_of(a) != _type_of(b):
        return False
    return a == b


def _escape_token(token) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


class SchemaRegistry:
    """Schemadokumente, adressiert über ihren Dateinamen."""

    def __init__(self, documents: dict[str, dict]):
        self.documents = documents
        self._patterns: dict[str, re.Pattern] = {}

    @classmethod
    def load_bundled(cls) -> "SchemaRegistry":
        documents = {}
        for name in SCHEMA_FILES:
            with open(os.path.join(SCHEMA_DIR, name), "r", encoding="utf-8") as handle:
                documents[name] = json.load(handle)
        registry = cls(documents)
        for name, document in documents.items():
            registry.assert_supported(document, name)
        return registry

    def _pattern(self, source: str) -> re.Pattern:
        if source not in self._patterns:
            self._patterns[source] = re.compile(source)
        return self._patterns[source]

    def resolve_ref(self, ref: str, from_file: str):
        file_part, _, pointer = ref.partition("#")
        target = from_file if file_part == "" else file_part
        if target not in self.documents:
            raise ValueError(f'Unbekanntes Schemadokument in $ref "{ref}" (aus {from_file}).')
        node = self.documents[target]
        for raw in [p for p in pointer.split("/")[1:]] if pointer else []:
            key = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise ValueError(f'$ref "{ref}" (aus {from_file}) zeigt auf keine Definition.')
            node = node[key]
        return node, target

    def assert_supported(self, schema, file: str, path: str = "#") -> None:
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise ValueError(f"{file}{path}: Schema muss ein Objekt oder ein Boolean sein.")
        for key in schema:
            if key not in _IGNORED and key not in _SUPPORTED:
                raise ValueError(f'{file}{path}: nicht unterstütztes Schlüsselwort "{key}".')
        if "$ref" in schema:
            self.resolve_ref(schema["$ref"], file)
        if "pattern" in schema:
            self._pattern(schema["pattern"])
        for key in ("not", "if", "then", "else", "items", "additionalProperties"):
            if key in schema:
                self.assert_supported(schema[key], file, f"{path}/{key}")
        for key in ("allOf", "anyOf", "oneOf"):
            for index, sub in enumerate(schema.get(key, [])):
                self.assert_supported(sub, file, f"{path}/{key}/{index}")
        for name, sub in schema.get("properties", {}).items():
            self.assert_supported(sub, file, f"{path}/properties/{_escape_token(name)}")
        for sub in schema.get("$defs", {}).values():
            self.assert_supported(sub, file, f"{path}/$defs")

    def validate(self, value, schema, file: str, path: str = "") -> list[dict]:
        errors: list[dict] = []
        self._validate(value, schema, file, path, errors)
        return errors

    def _validate(self, value, schema, file, path, errors) -> None:
        if schema is True:
            return
        if schema is False:
            errors.append({"path": path, "message": "an dieser Stelle ist kein Wert erlaubt"})
            return
        if "$ref" in schema:
            target, target_file = self.resolve_ref(schema["$ref"], file)
            self._validate(value, target, target_file, path, errors)
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_matches_type(value, t) for t in types):
                errors.append({"path": path, "message": f"erwartet {' | '.join(types)}, erhalten {_type_of(value)}"})
                return
        if "const" in schema and not _deep_equal(value, schema["const"]):
            errors.append({"path": path, "message": f"erwartet konstant {json.dumps(schema['const'])}"})
        if "enum" in schema and not any(_deep_equal(value, option) for option in schema["enum"]):
            errors.append({"path": path, "message": f"Wert {json.dumps(value)} nicht in enum"})

        if isinstance(value, str):
            length = len(value)
            if "minLength" in schema and length < schema["minLength"]:
                errors.append({"path": path, "message": f"kürzer als minLength {schema['minLength']}"})
            if "maxLength" in schema and length > schema["maxLength"]:
                errors.append({"path": path, "message": f"länger als maxLength {schema['maxLength']}"})
            if "pattern" in schema and not self._pattern(schema["pattern"]).search(value):
                errors.append({"path": path, "message": f"entspricht nicht pattern {schema['pattern']}"})

        if _type_of(value) == "number":
            for key, fails, word in (
                ("minimum", lambda v, b: v < b, "kleiner als minimum"),
                ("maximum", lambda v, b: v > b, "größer als maximum"),
                ("exclusiveMinimum", lambda v, b: v <= b, "nicht größer als exclusiveMinimum"),
                ("exclusiveMaximum", lambda v, b: v >= b, "nicht kleiner als exclusiveMaximum"),
            ):
                if key in schema and fails(value, schema[key]):
                    errors.append({"path": path, "message": f"{word} {schema[key]}"})

        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append({"path": path, "message": f"weniger als minItems {schema['minItems']}"})
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append({"path": path, "message": f"mehr als maxItems {schema['maxItems']}"})
            if schema.get("uniqueItems") is True:
                for i in range(len(value)):
                    for j in range(i + 1, len(value)):
                        if _deep_equal(value[i], value[j]):
                            errors.append({"path": path, "message": f"uniqueItems verletzt (Index {i} und {j})"})
            if "items" in schema:
                for index, item in enumerate(value):
                    self._validate(item, schema["items"], file, f"{path}/{index}", errors)

        if isinstance(value, dict):
            for name in schema.get("required", []):
                if name not in value:
                    errors.append({"path": path, "message": f'Pflichtfeld "{name}" fehlt'})
            declared = schema.get("properties", {})
            for name, entry in value.items():
                child = f"{path}/{_escape_token(name)}"
                if name in declared:
                    self._validate(entry, declared[name], file, child, errors)
                elif "additionalProperties" in schema:
                    if schema["additionalProperties"] is False:
                        errors.append({"path": child, "message": "unbekanntes Zusatzfeld"})
                    else:
                        self._validate(entry, schema["additionalProperties"], file, child, errors)

        for sub in schema.get("allOf", []):
            self._validate(value, sub, file, path, errors)
        if "anyOf" in schema:
            if not any(not self.validate(value, sub, file, path) for sub in schema["anyOf"]):
                errors.append({"path": path, "message": "kein Zweig von anyOf erfüllt"})
        if "oneOf" in schema:
            matches = sum(1 for sub in schema["oneOf"] if not self.validate(value, sub, file, path))
            if matches != 1:
                errors.append({"path": path, "message": f"oneOf erfordert genau einen Treffer, gefunden {matches}"})
        if "not" in schema and not self.validate(value, schema["not"], file, path):
            errors.append({"path": path, "message": "not-Bedingung verletzt"})
        if "if" in schema:
            branch = schema.get("then") if not self.validate(value, schema["if"], file, path) else schema.get("else")
            if branch is not None:
                self._validate(value, branch, file, path, errors)


_REGISTRY: SchemaRegistry | None = None


def registry() -> SchemaRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SchemaRegistry.load_bundled()
    return _REGISTRY


def validate_manifest(document: dict) -> list[str]:
    """Schema **und** die Regeln, die JSON Schema nicht ausdrücken kann.

    Dieselben Zusatzregeln wie ``validate.mjs`` und der Prüfer des Servers:
    je Rolle und je Pfad höchstens ein Eintrag, mindestens ein vorhandenes
    Asset, ``contentHash`` passt zum Inhalt, ``depth`` nie ``present``
    (QB-01, ``integrations/blender/docs/open-questions.md``).
    """
    reg = registry()
    schema = reg.documents["capture-manifest.schema.json"]
    problems = [
        f"{e['path'] or '/'}: {e['message']}"
        for e in reg.validate(document, schema, "capture-manifest.schema.json")
    ]
    version = str(document.get("contractVersion", "")) if isinstance(document, dict) else ""
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        major, minor, _patch = (int(x) for x in version.split("."))
        if major != 1:
            problems.append("/contractVersion: unsupported_contract_major")
        elif minor > 1:
            problems.append("/contractVersion: unsupported_contract_minor")
    if isinstance(document, dict) and version.startswith("1.0."):
        host = ((document.get("source") or {}).get("host") or {})
        if isinstance(host, dict) and "capabilities" in host:
            problems.append("/source/host/capabilities: in 1.0.x unzulässig (erst ab 1.1.0)")
    assets = document.get("assets") if isinstance(document, dict) else None
    if isinstance(assets, list):
        roles = [a.get("role") for a in assets if isinstance(a, dict)]
        paths = [a.get("path") for a in assets if isinstance(a, dict)]
        if len(set(roles)) != len(roles):
            problems.append("/assets: eine Rolle steht mehr als einmal")
        if len(set(paths)) != len(paths):
            problems.append("/assets: ein Pfad steht mehr als einmal")
        present = [a for a in assets if isinstance(a, dict) and a.get("status") == "present"]
        if not present:
            problems.append("/assets: kein Asset mit status present")
        if any(a.get("role") == "depth" for a in present):
            problems.append("/assets: depth ist für Blender nie present (QB-01)")
        try:
            if document.get("contentHash") != content_hash(assets):
                problems.append("/contentHash: passt nicht zum Inhalt der Assets")
        except (KeyError, TypeError):
            pass
    return problems


# --------------------------------------------------------------------------
# Aufbau
# --------------------------------------------------------------------------


@dataclass
class CaptureFile:
    """Eine erzeugte Bilddatei mit dem, was das Manifest über sie sagt."""

    role: str
    path: str  # relativ zur Capture-Wurzel, etwa images/beauty.png
    media_type: str
    image: dict
    note: str | None = None
    byte_size: int = 0
    sha256: str = ""


@dataclass
class PlannedRole:
    """Eine gewählte Rolle, die dieses Capture **nicht** als Datei trägt — mit Grund."""

    role: str
    path: str
    media_type: str | None
    note: str


@dataclass
class ManifestInput:
    capture_id: str
    created_at: str
    host_version: str
    plugin_version: str
    machine: dict
    capabilities: dict | None
    platform_project_id: str | None
    source_project_key: str | None
    project_display_name: str | None
    view_display_name: str | None
    files: list[CaptureFile] = field(default_factory=list)
    planned: list[PlannedRole] = field(default_factory=list)
    camera: dict | None = None
    geometry: dict | None = None
    # 1.0.0 nur, wenn der Server 1.1 nicht umsetzt; dann ohne ``capabilities`` (§3).
    contract_version: str = CONTRACT_VERSION


def asset_entries(data: ManifestInput) -> list[dict]:
    assets: list[dict] = []
    for f in data.files:
        entry = {
            "role": f.role,
            "path": f.path,
            "status": "present",
            "mediaType": f.media_type,
            "byteSize": f.byte_size,
            "sha256": f.sha256,
            "image": dict(f.image),
        }
        if f.note:
            entry["note"] = f.note[:512]
        assets.append(entry)
    for p in data.planned:
        entry = {"role": p.role, "path": p.path, "status": "planned"}
        if p.media_type:
            entry["mediaType"] = p.media_type
        entry["note"] = p.note[:512]
        assets.append(entry)
    return assets


def build_manifest(data: ManifestInput) -> dict:
    assets = asset_entries(data)
    host: dict = {"key": HOST_KEY, "version": data.host_version}
    if data.capabilities and data.contract_version != "1.0.0":
        host["capabilities"] = data.capabilities
    project: dict = {
        "platformProjectId": data.platform_project_id,
        "sourceProjectKey": data.source_project_key,
    }
    if data.project_display_name:
        project["displayName"] = data.project_display_name[:512]
    view: dict = {"sourceViewKey": None}
    if data.view_display_name:
        view["displayName"] = data.view_display_name[:512]
    return {
        "contract": CONTRACT,
        "contractVersion": data.contract_version,
        "captureId": data.capture_id,
        "createdAt": data.created_at,
        "source": {
            "host": host,
            "plugin": {"identifier": CLIENT_ID, "version": data.plugin_version},
            "machine": dict(data.machine),
        },
        "project": project,
        "view": view,
        "intent": {},
        # Ausbaustufe 2 (#177, RTX-P-011): export.py liefert heute None.
        "camera": data.camera,
        "geometry": data.geometry,
        "contentHash": content_hash(assets),
        "assets": assets,
    }


def serialize(manifest: dict) -> bytes:
    """Die Manifestbytes — **genau** die, deren SHA-256 die Anlage ankündigt."""
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def check_limits(manifest: dict, manifest_size: int, limits: dict | None) -> str | None:
    """Die Grenzen aus dem Handshake, lokal geprüft — wie ``CheckLimits`` in Archicad."""
    if not limits:
        return None
    present = [a for a in manifest["assets"] if a["status"] == "present"]
    allowed = limits.get("allowedMediaTypes") or []
    for asset in present:
        if limits.get("maxAssetBytes") and asset["byteSize"] > limits["maxAssetBytes"]:
            return f"Die Datei {asset['path']} ist größer, als der Server annimmt."
        if allowed and asset["mediaType"] not in allowed:
            return f"Der Server nimmt den Medientyp {asset['mediaType']} nicht an."
    if limits.get("maxAssetCount") and len(present) > limits["maxAssetCount"]:
        return "Mehr Dateien, als der Server je Capture annimmt."
    if limits.get("maxTotalBytes") and sum(a["byteSize"] for a in present) > limits["maxTotalBytes"]:
        return "Der Capture ist insgesamt zu groß."
    if limits.get("maxManifestBytes") and manifest_size > limits["maxManifestBytes"]:
        return "Das Manifest ist größer, als der Server annimmt."
    return None


# --------------------------------------------------------------------------
# PNG-Kopf — Maße aus der Datei, nicht aus einer Einstellung
# --------------------------------------------------------------------------

_PNG_CHANNELS = {0: "gray", 2: "rgb", 3: "rgb", 4: "gray-alpha", 6: "rgba"}


def describe_png(path: str) -> dict:
    """Breite, Höhe, Bittiefe und Kanäle aus dem IHDR eines PNG — abgelesen, nicht geraten."""
    with open(path, "rb") as handle:
        head = handle.read(33)
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError("Die Datei ist kein PNG.")
    width = int.from_bytes(head[16:20], "big")
    height = int.from_bytes(head[20:24], "big")
    depth, color_type = head[24], head[25]
    if color_type not in _PNG_CHANNELS:
        raise ValueError("Unbekannter PNG-Farbtyp.")
    return {
        "width": width,
        "height": height,
        "colorSpace": "srgb",
        "bitDepth": 8 if color_type == 3 else min(max(depth, 8), 16),
        "sampleFormat": "uint",
        "channels": _PNG_CHANNELS[color_type],
    }
