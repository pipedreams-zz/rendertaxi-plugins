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
* **IDs** (#289 Teil 2, gemessen 10.10.2026, ``docs/measurements/2026-10-10-corona-ids-buffers.md``) aus dem
  ID-Pass (Typ 13, Modus 10701: 4 Object, 3 Material) mit Anti-aliasing aus — **eine** Ebene je Rolle, gleich wie
  viele IDs: je Objekt bzw. Material eine flache Farbe, ohne Treffer (0, 0, 0). Die Farbe im Modus Material hängt nur
  am Namen des Materials (über Läufe gleich), im Modus Object ist sie je Lauf zufällig. Aus den Farben wird der Index
  (``colour_index``: nach Farbwert sortiert, 1 … N); Coronas eigene Zuordnung — Material ID (Parameter 4220) und
  Objektpuffer der Compositing-Tags — kommt aus den **aktivierten Masken des Nutzers** im selben Render (``active_masks``)
  und geht nur in die Begleitliste.
* **Masken** (Rolle ``mask``): die aktivierten Object-Buffer-Masken des Nutzers (Typ 14, „Object buffer ID“ 10811/10812)
  aus ihren eigenen Ebenen. Aktiv heißt: Multi-Pass an, der Knoten an und jeder Ordner (Typ 999) darüber an — ein
  ausgeschalteter Ordner liefert keine Ebene (gemessen).

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
NORMALS_GEOMETRY, WORLD_POSITION, ID_PASS, MASK, SOURCE_COLOR, FOLDER = 8, 11, 13, 14, 21, 999
ID_MODE, ID_MODE_MATERIAL, ID_MODE_OBJECT = 10701, 3, 4
COMPONENT, COMPONENT_DIFFUSE = 10601, 0
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


class UserMask:
    """Eine aktivierte Maske des Nutzers: ``kind`` ``object`` (Objektpuffer) oder ``material`` (Material ID)."""

    def __init__(self, kind: str, number: int, name: str):
        self.kind, self.number, self.name = kind, number, name

    def __eq__(self, other):
        return isinstance(other, UserMask) and (self.kind, self.number, self.name) == (other.kind, other.number,
                                                                                      other.name)

    def __repr__(self):
        return f"UserMask({self.kind!r}, {self.number}, {self.name!r})"


def _number(node, key) -> int | None:
    try:
        value = node[key]
    except (AttributeError, TypeError, KeyError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def active_masks(doc) -> list[UserMask]:
    """Die **aktivierten** Masken des Nutzers in der Reihenfolge des Pass-Baums — nur gelesen.

    Aktiv heißt: Multi-Pass an, der Knoten an und jeder Ordner darüber an (ein ausgeschalteter Ordner liefert keine
    Ebene, gemessen). Eine Maske ist ``object`` mit „Object buffer ID“ an, ``material`` mit „Material ID“ an; eine mit
    beidem schneidet zwei Mengen und zählt zu keiner.
    """
    hook, head = _head(doc)
    if hook is None or head is None or not hook[HOOK_ENABLE]:
        return []
    found: list[UserMask] = []

    def walk(node):
        while node is not None:
            # Am Host ist „Enable“ 0 oder 1 (gemessen 10.10.2026), nicht False/True; ein neuer Knoten hat keinen Wert.
            if node.GetType() == NODE and (node[ENABLE] is None or bool(node[ENABLE])):
                if node[TYPE] == FOLDER:
                    walk(node.GetDown())
                elif node[TYPE] == MASK:
                    on_object, on_material = bool(node[MASK_OBJECT_ON]), bool(node[MASK_MATERIAL_ON])
                    kind, key = (("object", MASK_OBJECT_ID) if on_object and not on_material
                                 else ("material", MASK_MATERIAL_ID) if on_material and not on_object else (None, None))
                    number = _number(node, key) if kind else None
                    if kind and number is not None:
                        found.append(UserMask(kind, number, node.GetName()))
            node = node.GetNext()

    walk(head.GetFirst())
    return found


def object_buffers(doc) -> list[UserMask]:
    """Die aktivierten Object-Buffer-Masken — die Liste, aus der der Nutzer die Masken wählt (je Buffer-ID die erste)."""
    seen: set[int] = set()
    found = []
    for mask in active_masks(doc):
        if mask.kind == "object" and mask.number not in seen:
            seen.add(mask.number)
            found.append(mask)
    return found


def material_names(doc) -> dict[int, list[str]]:
    """Material ID (Parameter 4220, ab 1) → Namen der Corona-Materialien mit dieser ID — nur gelesen."""
    found: dict[int, list[str]] = {}
    material = doc.GetFirstMaterial() if hasattr(doc, "GetFirstMaterial") else None
    while material is not None:
        if material.GetType() in MATERIAL_TYPES:
            try:
                value = material[MATERIAL_ID]
            except (AttributeError, TypeError, KeyError):
                value = None
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                found.setdefault(value, []).append(material.GetName())
        material = material.GetNext()
    return found


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


def prepare(twin, roles) -> Passes:
    """Die Pässe für ``roles`` in den Pass-Baum der **Kopie** ``twin`` — nie in das Dokument des Nutzers.

    Ist Multi-Pass in der Kopie aus, schaltet ``prepare`` es ein und die Pässe des Nutzers dort aus (sie würden sonst
    erstmals mitgerendert). Schlägt etwas fehl, steht der Grund in ``problem``; gerendert wird trotzdem (Regel 3).
    Die Masken (Rolle ``mask``) und die Namen der Begleitlisten kommen aus den Masken des Nutzers, die ohnehin
    mitgerendert werden — dafür legt ``prepare`` nichts an.
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
            wanted.append(("object-id", None, _node(ID_PASS, name("object-id"), False, {ID_MODE: ID_MODE_OBJECT})))
        if "material-id" in roles:
            wanted.append(("material-id", None, _node(ID_PASS, name("material-id"), False,
                                                      {ID_MODE: ID_MODE_MATERIAL})))
        for role, _number, node in wanted:
            node.InsertUnderLast(head)
            passes.layers[role] = node.GetName()
    except Exception as error:  # noqa: BLE001 — Regel 3: ohne Pässe bleibt die Beauty
        passes.layers = {}
        passes.problem = f"Corona-Pässe ließen sich nicht anlegen ({type(error).__name__})."
    return passes


# --------------------------------------------------------------------------
# Farbtabelle: aus einem ID-Pass mit Farbcodes ein Index je Pixel
# --------------------------------------------------------------------------


NO_HIT = (0.0, 0.0, 0.0)


class ColourTable:
    """Ergebnis von ``colour_index``: ``index`` je Pixel (0 = kein Index), ``colours`` je Index (1 … N) die Farbe,
    ``counts`` je Index die Pixelzahl, ``reassigned`` die Pixel, deren Farbe keine eigene Fläche war (Kanten)."""

    def __init__(self, index: list[int], colours: list[tuple], counts: list[int], reassigned: int):
        self.index, self.colours, self.counts, self.reassigned = index, colours, counts, reassigned


def _key(value, step: float) -> tuple:
    return tuple(int(round(c / step)) for c in value[:3])


def colour_index(pixels, width: int, tolerance: float = 0.0, step: float = 1e-4,
                 min_pixels: int = 1) -> ColourTable:
    """**Die eine Farbtabelle für beide ID-Pässe** (Objekt und Material): Farbe → Index 1 … N, (0, 0, 0) → 0.

    ``pixels`` zeilenweise (r, g, b). Der Index folgt dem **Farbwert** (aufsteigend) — für Materialien, deren Farbe
    am Namen hängt, damit unabhängig von Bildausschnitt und Reihenfolge. Ohne Anti-aliasing (so rendert das Plugin) ist
    jede Farbe eine Fläche; ``tolerance`` (je Kanal) und ``min_pixels`` sind für fremde Bilder **mit** Kantenglättung
    oder Rauschen (die Beispielbilder des Nutzers): Farben innerhalb der Toleranz zur häufigeren gehören zu ihr,
    seltenere als ``min_pixels`` sind Kanten und bekommen den häufigsten Index ihrer acht Nachbarn, sonst den der
    nächsten Farbe. Zwischenwerte gibt es im Ergebnis nie: jedes Pixel trägt genau einen Index.
    """
    keys = [_key(value, step) for value in pixels]
    counts: dict = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    reach = int(round(tolerance / step))
    centres: list = []
    owner: dict = {}
    for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if max(abs(c) for c in key) <= reach:  # schwarz oder fast schwarz: kein Treffer
            owner[key] = None
            continue
        near = next((centre for centre in centres if max(abs(a - b) for a, b in zip(key, centre)) <= reach), None)
        if near is not None:
            owner[key] = near
        elif count >= min_pixels:
            centres.append(key)
            owner[key] = key
    ordered = sorted(centres)
    number = {centre: position + 1 for position, centre in enumerate(ordered)}
    index = [0] * len(keys)
    pending = []
    for i, key in enumerate(keys):
        if key in owner:
            centre = owner[key]
            index[i] = 0 if centre is None else number[centre]
        else:
            pending.append(i)
    height = len(keys) // width if width else 0
    for i in pending:
        y, x = divmod(i, width)
        votes: dict = {}
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if (dx or dy) and 0 <= nx < width and 0 <= ny < height:
                    j = ny * width + nx
                    if keys[j] in owner:
                        votes[index[j]] = votes.get(index[j], 0) + 1
        if votes:
            index[i] = max(sorted(votes), key=lambda value: votes[value])
        elif ordered:
            key = keys[i]
            nearest = min(ordered, key=lambda centre: sum((a - b) ** 2 for a, b in zip(key, centre)))
            index[i] = number[nearest]
    totals = [0] * (len(ordered) + 1)
    for value in index:
        totals[value] += 1
    return ColourTable(index, [tuple(c * step for c in centre) for centre in ordered], totals[1:], len(pending))


def names_from_masks(table: ColourTable, masks, share: float = 0.9) -> dict:
    """Begleitliste aus Masken: ``masks`` sind ``(Name, Werte)``; ein Index, dessen Pixel zu mindestens ``share`` in
    **genau einer** Maske liegen (Wert über 0,5), heißt wie sie. Alles andere bleibt ohne Namen."""
    named: dict = {}
    hits: dict = {}
    for label, values in masks:
        inside: dict = {}
        for position, value in enumerate(values):
            if value > 0.5:
                number = table.index[position]
                if number:
                    inside[number] = inside.get(number, 0) + 1
        for number, count in inside.items():
            if count >= share * table.counts[number - 1]:
                hits.setdefault(number, []).append(label)
    for number, labels in hits.items():
        if len(labels) == 1:
            named[number] = labels[0]
    return named


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
