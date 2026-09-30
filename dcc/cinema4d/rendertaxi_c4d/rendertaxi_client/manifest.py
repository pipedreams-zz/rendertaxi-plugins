"""Capture-Manifest 1.1.0 bis 1.4.0 — erzeugen, hashen und **vor** dem Hochladen prüfen.

Kein Hostmodul. Die Python-Fassung dessen, was der Referenzclient
(``integrations/_shared/tools/capture-client.mjs``) und das Archicad-Add-on
(``core/src/CaptureManifest.cpp``) tun:

* ``content_hash`` ist zeichengleich zu ``canonical-hash.mjs`` — Assetzeile
  ``asset ⇥ role ⇥ path ⇥ sha256`` mit längenpräfigierten Feldern, sortiert
  nach UTF-8-Bytes, je Zeile ein Zeilenvorschub.
* ``SchemaRegistry`` ist eine Übertragung von ``json-schema.mjs``: dieselbe,
  bewusst begrenzte Teilmenge von JSON Schema 2020-12, ebenso streng — ein
  unbekanntes Schlüsselwort ist ein Fehler, kein stilles Ignorieren.
* Die Schemas liegen als Kopie unter ``schema/``;
  ``tests/contract/python-client.test.ts`` hält sie bytegleich zu
  ``integrations/_shared/contracts/v1``.

Schlüssel und Kennung des Plugins (``source.host.key``,
``source.plugin.identifier``) und die Rollen, die ein Host nie ``present``
meldet, kommen aus dem konfigurierten ``Host`` (``rendertaxi_client.configure``).

Ein Manifest, das hier nicht validiert, wird nicht hochgeladen
(``docs/api/plugin-api-v1.md``, Abschnitt 7.1, Schritt 4).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field

from . import CLIENT_DIR, current

CONTRACT = "rendertaxi.plugin.capture-manifest"
CONTRACT_VERSION = "1.1.0"
# Mit Modell: 1.2.0 bringt ``camera``, ``geometry`` und die Rolle ``model`` (ADR 0032).
MODEL_CONTRACT_VERSION = "1.2.0"
# 1.3.0: PNG ist das einzige Bildformat des Bildwegs, Datenpässe 8 oder 16 Bit ``uint``,
# Tiefe normalisiert (RTX-P-012, capture-manifest.md Abschnitt 12).
PNG_CONTRACT_VERSION = "1.3.0"
# 1.4.0: die Kamera trägt optional Objektiv (``lens``) und Shift (RTX-M2-026,
# capture-manifest.md Abschnitt 11.3).
CAMERA_CONTRACT_VERSION = "1.4.0"
MODEL_ROLE = "model"
MODEL_MEDIA_TYPE = "model/gltf-binary"
PNG_MEDIA_TYPE = "image/png"
IMPLEMENTED_MINOR = 4
PNG_SINCE_MINOR = 3
CAMERA_LENS_SINCE_MINOR = 4

# Bittiefe der Datenpässe — der Nutzer wählt im Plugin, Standard 8 Bit (Nutzerentscheidung
# vom 29.09.2026). Beide Plugins zeigen genau diesen Wortlaut.
DATA_PASS_BIT_DEPTHS = (8, 16)
DEFAULT_DATA_PASS_BIT_DEPTH = 8
DATA_PASS_BIT_DEPTH_LABEL = "Bittiefe der Datenpässe"
DATA_PASS_BIT_DEPTH_OPTIONS = ((8, "8 Bit (Standard)"), (16, "16 Bit"))

SCHEMA_DIR = os.path.join(CLIENT_DIR, "schema")
SCHEMA_FILES = (
    "capture-manifest.schema.json",
    "common.schema.json",
    "host-capabilities.schema.json",
)


def plugin_version() -> str:
    """Die Version des Plugins, das den Client konfiguriert hat (die eine Quelle liegt beim Plugin)."""
    return current().plugin_version


def _highest_minor(handshake: dict | None) -> int | None:
    negotiation = (handshake or {}).get("negotiation") or {}
    match = re.fullmatch(r"1\.([0-9]+)\.[0-9]+", str(negotiation.get("highestSupportedVersion") or ""))
    return int(match.group(1)) if match else None


def image_contract_version(handshake: dict | None) -> str:
    """Vertragsfassung des Bildwegs (§3, Regel 2): 1.3.0, wenn der Server sie umsetzt, sonst 1.1.0;
    gegen einen Server mit höchstens 1.0 dann 1.0.0. Ohne Angabe 1.1.0."""
    minor = _highest_minor(handshake)
    if minor is None:
        return CONTRACT_VERSION
    if minor >= PNG_SINCE_MINOR:
        return PNG_CONTRACT_VERSION
    return "1.0.0" if minor == 0 else CONTRACT_VERSION


def model_contract_version(handshake: dict | None) -> str:
    """Vertragsfassung mit Modell: die höchste, die der Server umsetzt — 1.4.0 (Objektiv und
    Shift der Kamera), 1.3.0 (PNG) oder 1.2.0 (ADR 0032)."""
    minor = _highest_minor(handshake)
    if minor is not None and minor >= CAMERA_LENS_SINCE_MINOR:
        return CAMERA_CONTRACT_VERSION
    return PNG_CONTRACT_VERSION if minor is not None and minor >= PNG_SINCE_MINOR else MODEL_CONTRACT_VERSION


def camera_lens_allowed(contract_version: str) -> bool:
    """Ob ein Dokument dieser Fassung ``camera.lens`` und ``camera.shift`` tragen darf (ab 1.4.0)."""
    match = re.fullmatch(r"1\.([0-9]+)\.[0-9]+", str(contract_version))
    return match is not None and int(match.group(1)) >= CAMERA_LENS_SINCE_MINOR


def data_pass_bit_depth(value) -> int:
    """Die gemerkte Bittiefe der Datenpässe — jeder ungültige Wert wird zum Standard 8 Bit.

    Ein gemerkter Zustand darf das Laden nie verhindern (Regel 3 aus #190): kein Fehler,
    kein Abbruch, nur der Standard.
    """
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_DATA_PASS_BIT_DEPTH
    return number if number in DATA_PASS_BIT_DEPTHS else DEFAULT_DATA_PASS_BIT_DEPTH


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
    **Bild**, ``contentHash`` passt zum Inhalt, eine Rolle aus
    ``Host.never_present`` nie ``present`` (Blender: ``depth``, QB-01); seit 1.2.0
    ``geometry`` genau dann, wenn eine Datei der Rolle ``model`` vorhanden ist,
    und die Kamera mit normierten, nicht parallelen Vektoren; seit 1.4.0 ein
    Objektiv, das denselben Winkel beschreibt wie ``fieldOfView``. Das
    PNG-Profil ab 1.3.0 steht im Schema selbst (``pngProfileAsset``).
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
        elif minor > IMPLEMENTED_MINOR:
            problems.append("/contractVersion: unsupported_contract_minor")
    if isinstance(document, dict) and version.startswith("1.0."):
        host = ((document.get("source") or {}).get("host") or {})
        if isinstance(host, dict) and "capabilities" in host:
            problems.append("/source/host/capabilities: in 1.0.x unzulässig (erst ab 1.1.0)")
    assets = document.get("assets") if isinstance(document, dict) else None
    if isinstance(document, dict) and re.fullmatch(r"1\.[01]\.[0-9]+", version):
        for block in ("camera", "geometry"):
            if document.get(block) is not None:
                problems.append(f"/{block}: ein Objekt ist erst ab contractVersion 1.2.0 zulässig")
        for index, asset in enumerate(assets if isinstance(assets, list) else []):
            if isinstance(asset, dict) and asset.get("role") == "model":
                problems.append(f"/assets/{index}/role: model ist erst ab contractVersion 1.2.0 zulässig")
    camera = document.get("camera") if isinstance(document, dict) else None
    if isinstance(camera, dict) and re.fullmatch(r"1\.[0-3]\.[0-9]+", version):
        for key in ("lens", "shift"):
            if key in camera:
                problems.append(f"/camera/{key}: erst ab contractVersion 1.4.0 zulässig")
    if isinstance(assets, list):
        roles = [a.get("role") for a in assets if isinstance(a, dict)]
        paths = [a.get("path") for a in assets if isinstance(a, dict)]
        if len(set(roles)) != len(roles):
            problems.append("/assets: eine Rolle steht mehr als einmal")
        if len(set(paths)) != len(paths):
            problems.append("/assets: ein Pfad steht mehr als einmal")
        present = [a for a in assets if isinstance(a, dict) and a.get("status") == "present"]
        if not any(a.get("role") != MODEL_ROLE for a in present):
            problems.append("/assets: kein Bild mit status present — das Modell allein ist kein Capture")
        for index, asset in enumerate(assets):
            depth = asset.get("depth") if isinstance(asset, dict) else None
            near, far = (depth.get("near"), depth.get("far")) if isinstance(depth, dict) else (None, None)
            if isinstance(near, (int, float)) and isinstance(far, (int, float)) and far <= near:
                problems.append(f"/assets/{index}/depth/far: ist nicht größer als near")
        host = current()
        for role, question in sorted(host.never_present.items()):
            if any(a.get("role") == role for a in present):
                problems.append(f"/assets: {role} ist für {host.label} nie present ({question})")
        try:
            if document.get("contentHash") != content_hash(assets):
                problems.append("/contentHash: passt nicht zum Inhalt der Assets")
        except (KeyError, TypeError):
            pass
        problems.extend(_geometry_problems(document, assets))
    if isinstance(document, dict):
        problems.extend(_camera_problems(document.get("camera")))
        geometry = document.get("geometry")
        georeference = geometry.get("georeference") if isinstance(geometry, dict) else None
        if isinstance(georeference, dict):
            problems.extend(_transform_chain_problems(georeference.get("transformChain")))
    return problems


def _transform_chain_problems(chain) -> list[str]:
    """Geordnet und geschlossen von ``model`` nach ``project`` — wie ``checkTransformChain``."""
    if not isinstance(chain, list) or not chain:
        return []
    pointer = "/geometry/georeference/transformChain"
    steps = [step if isinstance(step, dict) else {} for step in chain]
    problems = []
    if not all(step.get("order") == index + 1 for index, step in enumerate(steps)):
        problems.append(f"{pointer}: order zählt nicht lückenlos ab 1")
    linked = all(index == 0 or steps[index - 1].get("targetFrame") == step.get("sourceFrame")
                 for index, step in enumerate(steps))
    if steps[0].get("sourceFrame") != "model" or steps[-1].get("targetFrame") != "project" or not linked:
        problems.append(f"{pointer}: führt nicht lückenlos von model nach project")
    return problems


def _geometry_problems(document: dict, assets: list) -> list[str]:
    """``geometry`` beschreibt genau die eine GLB-Datei — und steht genau dann da."""
    model = next((a for a in assets if isinstance(a, dict) and a.get("role") == MODEL_ROLE
                  and a.get("status") == "present"), None)
    geometry = document.get("geometry")
    described = isinstance(geometry, dict)
    if model is not None and not described:
        return ["/geometry: ein Asset mit role model und status present verlangt den Block geometry"]
    if described and model is None:
        return ["/geometry: der Block verlangt ein Asset mit role model und status present"]
    if described and geometry.get("assetPath") != model.get("path"):
        return ["/geometry/assetPath: zeigt nicht auf das Asset mit role model"]
    return []


_UNIT_TOLERANCE = 1e-6
# Wie ``LENS_ANGLE_TOLERANCE`` in ``validate.mjs``: Rundung von Brennweite und Sensor.
_LENS_ANGLE_TOLERANCE = 2e-6


def _camera_problems(camera) -> list[str]:
    """Was JSON Schema nicht ausdrücken kann — wortgleich zu ``checkCamera`` in ``validate.mjs``."""
    if not isinstance(camera, dict):
        return []
    problems = []
    vectors = {}
    for key in ("direction", "up"):
        vector = camera.get(key)
        if not isinstance(vector, list) or len(vector) != 3 or not all(_type_of(c) == "number" for c in vector):
            continue
        vectors[key] = vector
        if abs(sum(c * c for c in vector) ** 0.5 - 1) > _UNIT_TOLERANCE:
            problems.append(f"/camera/{key}: kein normierter Vektor")
    if "direction" in vectors and "up" in vectors:
        dot = sum(a * b for a, b in zip(vectors["direction"], vectors["up"]))
        if abs(dot) >= 1 - _UNIT_TOLERANCE:
            problems.append("/camera/up: parallel zu direction")
    clip = camera.get("clip") if isinstance(camera.get("clip"), dict) else {}
    near, far = clip.get("near"), clip.get("far")
    if _type_of(near) == "number" and _type_of(far) == "number" and far <= near:
        problems.append("/camera/clip/far: ist nicht größer als near")
    lens = camera.get("lens") if isinstance(camera.get("lens"), dict) else {}
    fov = camera.get("fieldOfView") if isinstance(camera.get("fieldOfView"), dict) else {}
    focal, sensor, angle = lens.get("focalLengthMm"), lens.get("sensorWidthMm"), fov.get("angle")
    if all(_type_of(v) == "number" for v in (focal, sensor, angle)) and focal > 0:
        if abs(2 * math.atan(sensor / (2 * focal)) - angle) > _LENS_ANGLE_TOLERANCE:
            problems.append("/camera/lens: beschreibt einen anderen Winkel als fieldOfView")
        if lens.get("sensorFit") not in ("auto", fov.get("axis")):
            problems.append("/camera/lens/sensorFit: nennt eine andere Achse als fieldOfView")
    return problems


# --------------------------------------------------------------------------
# Aufbau
# --------------------------------------------------------------------------


@dataclass
class CaptureFile:
    """Eine erzeugte Datei mit dem, was das Manifest über sie sagt.

    Ein Bild trägt ``image``; die GLB-Datei der Rolle ``model`` nicht (§11.1).
    """

    role: str
    path: str  # relativ zur Capture-Wurzel, etwa images/beauty.png
    media_type: str
    image: dict | None
    note: str | None = None
    byte_size: int = 0
    sha256: str = ""
    # Pflicht bei ``present`` für die Rollen ``depth`` bzw. ``normal`` (§5.2, §5.3).
    depth: dict | None = None
    normal: dict | None = None


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


def capture_file(path: str, root: str, role: str, media_type: str, image: dict | None,
                 note: str | None, depth: dict | None = None, normal: dict | None = None) -> CaptureFile:
    """Eine geschriebene Datei als ``CaptureFile`` — Hash und Größe aus der Datei, Pfad relativ zur Wurzel."""
    sha, size = sha256_file(path)
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    return CaptureFile(role=role, path=relative, media_type=media_type, image=image,
                       note=note, byte_size=size, sha256=sha, depth=depth, normal=normal)


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
        }
        if f.image is not None:
            entry["image"] = dict(f.image)
        if f.depth is not None:
            entry["depth"] = dict(f.depth)
        if f.normal is not None:
            entry["normal"] = dict(f.normal)
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
    plugin = current()
    host: dict = {"key": plugin.key, "version": data.host_version}
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
            "plugin": {"identifier": plugin.client_id, "version": data.plugin_version},
            "machine": dict(data.machine),
        },
        "project": project,
        "view": view,
        "intent": {},
        # Seit 1.2.0 aus dem Modellweg des Hosts (Blender: export.py, RTX-B-003; Cinema 4D:
        # rendertaxi_c4d/export.py, RTX-C4D-003); ohne Modell null.
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
        if asset["role"] == MODEL_ROLE:
            # Die Modelldatei hat ihre eigene Grenze (``maxGeometryBytes``, RTX-P-011).
            cap = limits.get("maxGeometryBytes") or limits.get("maxAssetBytes")
            if cap and asset["byteSize"] > cap:
                return (f"Das Modell ist {asset['byteSize'] / 1048576:.1f} MB groß; "
                        f"der Server nimmt höchstens {cap / 1048576:.1f} MB an.")
        elif limits.get("maxAssetBytes") and asset["byteSize"] > limits["maxAssetBytes"]:
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
