"""Corona-Multipass — eigene Pässe in der Kopie des Dokuments, gelesen aus dem Bildspeicher (RTX-C4D-009, #289).

Am Host gemessen (Cinema 4D 2026.3.1, Corona 15, ``docs/measurements/2026-10-08-corona-multipass.md``,
``tools/measure_corona_multipass.py``):

* **Ablage.** Der Scene-Hook 1037467 trägt „Enable multi-pass“ (10000) und im Zweig 1037373 die Pässe als Knoten
  1033780: Typ 10101, Enable 10103, **Anti-aliasing 10105** (je Pass). Die Ebene in der ``MultipassBitmap`` heißt wie
  der Knoten; ``USERID`` ist für alle 111. Das Plugin legt eigene Knoten mit eigenem Namen an — **nur in der Kopie**,
  die ``pictureviewer.Rendering`` rendert; Pass-Baum und Einstellungen des Nutzers bleiben, wie sie sind (Regel 2).
* **Farbe.** Corona rechnet intern linear Rec.709 und gibt **jede** Ebene wie eine Farbe aus, unabhängig von den
  Render-Flags: mit OCIO über „linear → Renderraum“ des Dokuments (``COLORSPACETRANSFORMATION_OCIO_LINEAR_TO_RENDERING``;
  ACEScg: eine Matrix), mit Farbmanagement Basis und linearem Workflow unverändert, **ohne** linearen Workflow mit der
  sRGB-OETF je Kanal (dort meldet ``GetColorConverter`` trotzdem die Einheit — der Modus kommt aus den
  Projekteinstellungen). Zurückgerechnet wird mit der Gegenrichtung bzw. der sRGB-EOTF: Weltposition und Normalen auf
  0,0002 cm bzw. 1e-5 genau. Masken mit 0 und 1 ändert keine der drei Umrechnungen.
* **Tiefe** aus WorldPosition (Typ 11) mit Anti-aliasing aus: planar entlang der Blickachse, an Wänden auf 0,0008 cm,
  ohne Mischwerte an Silhouetten. ZDepth (Typ 12) taugt nicht: Strahlabstand, und seine Spanne (Corona-Datentyp
  1033966) ist aus Python weder lesbar noch setzbar.
* **Normalen** aus NormalsGeometry (Typ 8): Weltraum, ``(n + 1) / 2``, ohne Treffer (0, 0, 0).
* **Albedo** aus SourceColor (Typ 21) mit Component „Diffuse“ (10601 = 0): die diffuse Grundfarbe, die Corona rendert
  (Faktor 0,966 der Grundfarbe beim Standardmaterial), linear im Renderraum wie die Albedo unter Standard. Der Pass
  „Albedo“ (Typ 16) ist eine graue Diagnose und keine Grundfarbe.
* **IDs** aus Masken (Typ 14, Monochrom) mit Anti-aliasing aus, je ID eine: „Object buffer ID“ (10811/10812) sind die
  Objektpuffer der Compositing-Tags, „Material ID“ (10813/10814) die Material-ID der Corona-Materialien (Parameter 4220).
  Gemessen: nur 0 und 1, keine Überlappung, deckungsgleich mit den Flächen des ID-Passes. Der ID-Pass selbst (Typ 13)
  liefert Farbcodes, keinen Index.

Nur Python-Standardbibliothek und ``c4d``. Was hier nicht geht, wirft nie: die Rolle wird ``planned`` mit Grund
(Regel 3).
"""

from __future__ import annotations

import c4d

CORONA = 1030480
MULTIPASS_VIDEOPOST = 1033700
HOOK = 1037467
BRANCH = 1037373
NODE = 1033780
HOOK_ENABLE = 10000
TYPE, ENABLE, ANTIALIASING = 10101, 10103, 10105
NORMALS_GEOMETRY, WORLD_POSITION, MASK, SOURCE_COLOR = 8, 11, 14, 21
COMPONENT, COMPONENT_DIFFUSE = 10601, 0
MASK_MODE, MASK_MONOCHROME = 10801, 0
MASK_OBJECT_ON, MASK_OBJECT_ID, MASK_MATERIAL_ON, MASK_MATERIAL_ID = 10811, 10812, 10813, 10814
# Die Material-ID der Corona-Materialien (Physical, Legacy, Light): Parameter 4220 „Material ID“ unter „Advanced“.
MATERIAL_ID = 4220
MATERIAL_TYPES = (1056306, 1032100, 1035372)
NAME_PREFIX = "rendertaxi"

# Farbmanagement des Dokuments (Projekteinstellungen); fehlt eine Konstante, gilt OCIO nicht als erkannt.
COLOR_MANAGEMENT = getattr(c4d, "DOCUMENT_COLOR_MANAGEMENT", None)
COLOR_MANAGEMENT_OCIO = getattr(c4d, "DOCUMENT_COLOR_MANAGEMENT_OCIO", None)
LINEAR_WORKFLOW = getattr(c4d, "DOCUMENT_LINEARWORKFLOW", None)
LINEAR_TO_RENDERING = getattr(c4d, "COLORSPACETRANSFORMATION_OCIO_LINEAR_TO_RENDERING", None)
RENDERING_TO_LINEAR = getattr(c4d, "COLORSPACETRANSFORMATION_OCIO_RENDERING_TO_LINEAR", None)

OCIO, BASIC_LINEAR, BASIC_SRGB = "ocio", "basic-linear", "basic-srgb"
COLOR_HINT = "Projekteinstellungen › Farbmanagement: einen linearen Renderraum wählen (etwa ACEScg)."


def available() -> bool:
    return c4d.plugins.FindPlugin(MULTIPASS_VIDEOPOST, c4d.PLUGINTYPE_VIDEOPOST) is not None


def _head(doc):
    hook = doc.FindSceneHook(HOOK) if hasattr(doc, "FindSceneHook") else None
    if hook is None:
        return None, None
    heads = [branch["head"] for branch in (hook.GetBranchInfo() or []) if branch["id"] == BRANCH]
    return hook, (heads[0] if heads else None)


def _nodes(head):
    found = []

    def walk(node):
        while node is not None:
            if node.GetType() == NODE:
                found.append(node)
            walk(node.GetDown())
            node = node.GetNext()

    walk(head.GetFirst())
    return found


def material_ids(doc) -> list[int]:
    """Die Material-IDs der Corona-Materialien im Dokument (ab 1), aufsteigend — nur gelesen."""
    found = set()
    material = doc.GetFirstMaterial() if hasattr(doc, "GetFirstMaterial") else None
    while material is not None:
        if material.GetType() in MATERIAL_TYPES:
            try:
                value = material[MATERIAL_ID]
            except (AttributeError, TypeError, KeyError):
                value = None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                found.add(value)
        material = material.GetNext()
    return sorted(found)


# --------------------------------------------------------------------------
# Farbe: aus dem Wert der Ebene wieder den linearen Wert
# --------------------------------------------------------------------------


def _eotf(value: float) -> float:
    """sRGB-EOTF, unter 0,04045 linear — auch für negative Werte (so kodiert Corona sie, gemessen)."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _matrix(converter, transform):
    columns = [converter.TransformColor(c4d.Vector(*axis), transform) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
    return [(columns[0].x, columns[1].x, columns[2].x), (columns[0].y, columns[1].y, columns[2].y),
            (columns[0].z, columns[1].z, columns[2].z)]


def _apply(m, v):
    return tuple(row[0] * v[0] + row[1] * v[1] + row[2] * v[2] for row in m)


class Colour:
    """Wie aus dem Wert einer Corona-Ebene der lineare Wert wird — ``decode`` ist ``None``, wenn das nicht sicher geht.

    ``mode`` ist ``ocio``, ``basic-linear`` oder ``basic-srgb``; ``albedo`` ist, was auf die Albedo angewandt wird: sie
    bleibt im Renderraum wie unter Standard, nur die OETF wird zurückgerechnet.
    """

    def __init__(self, mode: str, decode, albedo, problem: str | None = None, label: str = ""):
        self.mode, self.decode, self.albedo, self.problem, self.label = mode, decode, albedo, problem, label


def colour(doc) -> Colour:
    if None in (COLOR_MANAGEMENT, COLOR_MANAGEMENT_OCIO, LINEAR_WORKFLOW):
        # Ohne die Konstanten ist der Modus nicht lesbar — nicht raten (Regel 3): nur die Albedo geht, unverändert.
        return Colour(OCIO, None, lambda v: tuple(v), COLOR_HINT, "Farbmanagement nicht lesbar")
    manager = doc[COLOR_MANAGEMENT]
    if manager == COLOR_MANAGEMENT_OCIO:
        problem = None
        try:
            converter = doc.GetColorConverter()
            forward = _matrix(converter, LINEAR_TO_RENDERING)
            back = _matrix(converter, RENDERING_TO_LINEAR)
            # Nur eine Matrix ist sicher umkehrbar: Hin und Rück muss die Einheit sein, und ein großer Vektor mit
            # Vorzeichen (wie eine Weltposition) muss sich wie die Matrix verhalten.
            probe = (1234.5, -678.25, 4321.0)
            moved = converter.TransformColor(c4d.Vector(*probe), LINEAR_TO_RENDERING)
            linear = all(abs(a - b) <= 1e-4 * max(1.0, abs(b)) for a, b in zip((moved.x, moved.y, moved.z),
                                                                                    _apply(forward, probe)))
            identity = _apply(back, _apply(forward, probe))
            inverse = all(abs(a - b) <= 1e-4 * max(1.0, abs(b)) for a, b in zip(identity, probe))
            if not (linear and inverse):
                problem = COLOR_HINT
        except Exception:  # noqa: BLE001 — ohne Umrechnung keine Tiefe und Normalen (Regel 3)
            problem, back = COLOR_HINT, None
        if problem:
            return Colour(OCIO, None, lambda v: tuple(v), problem, "OCIO, Renderraum nicht umkehrbar")
        return Colour(OCIO, lambda v, back=back: _apply(back, v), lambda v: tuple(v), None,
                      "OCIO: Ebene im Renderraum, Rückrechnung mit „Renderraum → linear“ des Dokuments")
    workflow = doc[LINEAR_WORKFLOW]  # am Host 0 oder 1 (gemessen), nicht False/True
    if workflow is None or bool(workflow):
        return Colour(BASIC_LINEAR, lambda v: tuple(v), lambda v: tuple(v), None, "Basis, linearer Workflow: unverändert")

    def srgb(v):
        return tuple(_eotf(c) for c in v)

    return Colour(BASIC_SRGB, srgb, srgb, None, "Basis ohne linearen Workflow: sRGB-kodiert, Rückrechnung mit der sRGB-EOTF")


# --------------------------------------------------------------------------
# Pässe in der Kopie
# --------------------------------------------------------------------------


def layer_name(role: str, number: int | None = None) -> str:
    return f"{NAME_PREFIX} {role}" if number is None else f"{NAME_PREFIX} {role} {number}"


def unique_name(role: str, number: int | None, taken: set) -> str:
    """Die **eine** Stelle der Namensvergabe (Review F-01): ein Name, den kein Pass der Kopie trägt — weder ein Pass des
    Nutzers (auch ausgeschaltet oder in einem Ordner) noch ein schon vergebener eigener. Belegt ist ein Name, dann
    „… (2)“, „… (3)“ usw. Der vergebene Name kommt in ``taken``.
    """
    base = layer_name(role, number)
    name, count = base, 1
    while name in taken:
        count += 1
        name = f"{base} ({count})"
    taken.add(name)
    return name


class Passes:
    """Die Pässe, die ``prepare`` in die Kopie gelegt hat: Rolle → tatsächlicher Ebenenname (IDs: Nummer → Ebenenname)."""

    def __init__(self, colour_info: Colour):
        self.colour = colour_info
        self.layers: dict = {}
        self.object_ids: list[int] = []
        self.material_ids: list[int] = []
        self.problem: str | None = None


def _node(kind: int, name: str, antialiasing: bool, params: dict | None = None):
    node = c4d.BaseObject(NODE)
    if node is None:
        raise RuntimeError("Corona-Pass nicht anlegbar")
    node[TYPE] = kind
    node[ENABLE] = True
    node[ANTIALIASING] = antialiasing
    for key, value in (params or {}).items():
        node[key] = value
    node.SetName(name)
    return node


def prepare(twin, roles, object_ids: list[int], material_ids_: list[int]) -> Passes:
    """Die Pässe für ``roles`` in den Pass-Baum der **Kopie** ``twin`` — nie in das Dokument des Nutzers.

    Ist Multi-Pass in der Kopie aus, schaltet ``prepare`` es ein und die Pässe des Nutzers dort aus (sie würden sonst
    erstmals mitgerendert). Schlägt etwas fehl, steht der Grund in ``problem``; gerendert wird trotzdem (Regel 3).
    """
    passes = Passes(colour(twin))
    try:
        hook, head = _head(twin)
        if hook is None or head is None:
            passes.problem = "Corona-Multi-Pass in diesem Dokument nicht gefunden."
            return passes
        existing = _nodes(head)
        if not hook[HOOK_ENABLE]:
            for node in existing:
                node[ENABLE] = False
            hook[HOOK_ENABLE] = True
        taken = {node.GetName() for node in existing}

        def name(role, number=None):
            return unique_name(role, number, taken)

        wanted = []
        if "depth" in roles and passes.colour.decode is not None:
            wanted.append(("depth", None, _node(WORLD_POSITION, name("depth"), False)))
        if "normal" in roles and passes.colour.decode is not None:
            wanted.append(("normal", None, _node(NORMALS_GEOMETRY, name("normal"), True)))
        if "albedo" in roles:
            wanted.append(("albedo", None, _node(SOURCE_COLOR, name("albedo"), True, {COMPONENT: COMPONENT_DIFFUSE})))
        if "object-id" in roles:
            passes.object_ids = list(object_ids)
            for number in object_ids:
                wanted.append(("object-id", number, _node(MASK, name("object-id", number), False, {
                    MASK_MODE: MASK_MONOCHROME, MASK_OBJECT_ON: True, MASK_OBJECT_ID: number})))
        if "material-id" in roles:
            passes.material_ids = list(material_ids_)
            for number in material_ids_:
                wanted.append(("material-id", number, _node(MASK, name("material-id", number), False, {
                    MASK_MODE: MASK_MONOCHROME, MASK_MATERIAL_ON: True, MASK_MATERIAL_ID: number})))
        for role, number, node in wanted:
            node.InsertUnderLast(head)
            if number is None:
                passes.layers[role] = node.GetName()
            else:
                passes.layers.setdefault(role, {})[number] = node.GetName()
    except Exception as error:  # noqa: BLE001 — Regel 3: ohne Pässe bleibt die Beauty
        passes.layers = {}
        passes.problem = f"Corona-Pässe ließen sich nicht anlegen ({type(error).__name__})."
    return passes


class AmbiguousLayer(Exception):
    """Mehr als eine Ebene trägt den Namen eines eigenen Passes — welche die eigene ist, lässt sich nicht sagen."""


def find_layer(bitmap, name: str):
    """Die **eine** Ebene mit diesem Namen (dem eigenen, kollisionsfrei vergebenen Knotennamen), ``None`` ohne sie.

    Tragen mehrere Ebenen den Namen, wird keine übernommen (Review F-01): ``AmbiguousLayer``.
    """
    found = []
    for index in range(bitmap.GetLayerCount()):
        layer = bitmap.GetLayerNum(index)
        if layer is not None and layer.GetParameter(c4d.MPBTYPE_NAME) == name:
            found.append(layer)
    if len(found) > 1:
        raise AmbiguousLayer(name)
    return found[0] if found else None
