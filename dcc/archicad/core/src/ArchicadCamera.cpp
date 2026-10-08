#include "rtx/ArchicadCamera.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>

#include "rtx/Json.hpp"

namespace rtx {
namespace {

/** Nicht `M_PI`: MSVC kennt es nur mit `_USE_MATH_DEFINES` (QA-07). */
constexpr double kPi = 3.14159265358979323846;

struct V3 {
	double x = 0, y = 0, z = 0;
};

V3 Add (V3 a, V3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
V3 Sub (V3 a, V3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
V3 Scale (V3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
V3 Cross (V3 a, V3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
double Dot (V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
double Len (V3 a) { return std::sqrt (Dot (a, a)); }
V3 Norm (V3 a)
{
	const double l = Len (a);
	return l > 1e-12 ? Scale (a, 1.0 / l) : V3 {};
}

/** Archicad (x, y, z) → glTF (x, z, −y). */
V3 ToGltf (V3 a) { return {a.x, a.z, -a.y}; }

void Put (double out[3], V3 v)
{
	out[0] = v.x;
	out[1] = v.y;
	out[2] = v.z;
}

/** Quaternion (x, y, z, w) aus einer Drehmatrix mit den Spalten r, u, b. */
void Quaternion (V3 r, V3 u, V3 b, double q[4])
{
	const double m00 = r.x, m01 = u.x, m02 = b.x;
	const double m10 = r.y, m11 = u.y, m12 = b.y;
	const double m20 = r.z, m21 = u.z, m22 = b.z;
	const double trace = m00 + m11 + m22;
	if (trace > 0) {
		const double s = 0.5 / std::sqrt (trace + 1.0);
		q[3] = 0.25 / s;
		q[0] = (m21 - m12) * s;
		q[1] = (m02 - m20) * s;
		q[2] = (m10 - m01) * s;
	} else if (m00 > m11 && m00 > m22) {
		const double s = 2.0 * std::sqrt (1.0 + m00 - m11 - m22);
		q[3] = (m21 - m12) / s;
		q[0] = 0.25 * s;
		q[1] = (m01 + m10) / s;
		q[2] = (m02 + m20) / s;
	} else if (m11 > m22) {
		const double s = 2.0 * std::sqrt (1.0 + m11 - m00 - m22);
		q[3] = (m02 - m20) / s;
		q[0] = (m01 + m10) / s;
		q[1] = 0.25 * s;
		q[2] = (m12 + m21) / s;
	} else {
		const double s = 2.0 * std::sqrt (1.0 + m22 - m00 - m11);
		q[3] = (m10 - m01) / s;
		q[0] = (m02 + m20) / s;
		q[1] = (m12 + m21) / s;
		q[2] = 0.25 * s;
	}
}

std::string Number (double value)
{
	if (!std::isfinite (value) || value == 0.0) return "0";
	char buffer[40];
	std::snprintf (buffer, sizeof (buffer), "%.9g", value);
	return buffer;
}

/** Shift auf sechs Stellen (Präzisionsklasse `ratio`), wie er im Manifest steht — Datei und Manifest sagen dasselbe. */
double RatioCanon (double value)
{
	const double rounded = std::round (value * 1e6) / 1e6;
	return rounded == 0.0 ? 0.0 : rounded;
}

/** Tiefe der acht Ecken der Hülle entlang `dir` ab `eye` — (nächste, fernste). */
void SceneDepths (const SceneBox& scene, V3 eye, V3 dir, double& nearest, double& farthest)
{
	nearest = std::numeric_limits<double>::max ();
	farthest = std::numeric_limits<double>::lowest ();
	for (int i = 0; i < 8; ++i) {
		const V3 corner {(i & 1) ? scene.max[0] : scene.min[0], (i & 2) ? scene.max[1] : scene.min[1],
						 (i & 4) ? scene.max[2] : scene.min[2]};
		const double depth = Dot (Sub (corner, eye), dir);
		nearest = std::min (nearest, depth);
		farthest = std::max (farthest, depth);
	}
}

} // namespace

Result<MappedCamera> MapArchicadCamera (const ArchicadProjection& p, const SceneBox& scene, const std::string& name,
										const std::string& source, int width, int height, bool withResolution)
{
	MappedCamera out;
	GlbCamera& cam = out.gltf;
	CaptureCamera& block = out.manifest;
	cam.name = name;

	if (width <= 0 || height <= 0) {
		width = p.hSize;
		height = p.vSize;
	}
	if (width <= 0 || height <= 0)
		return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Die Ansicht nennt keine Bildgröße.");
	const double aspect = static_cast<double> (width) / static_cast<double> (height);
	cam.aspectRatio = aspect;
	// Die Bildgröße ist ein **mittiger Zuschnitt** des 3D-Fensters auf ihr Seitenverhältnis, so groß wie
	// möglich — wie das Bild des Bildwegs, das auf die Rendering-Szene zugeschnitten wird
	// (`CropImageToAspect`). Schmaler als das Fenster: die Höhe bleibt, die Breite schrumpft; breiter:
	// die Breite bleibt, die Höhe schrumpft.
	const double windowAspect =
		p.hSize > 0 && p.vSize > 0 ? static_cast<double> (p.hSize) / static_cast<double> (p.vSize) : aspect;
	const bool keepsHeight = aspect <= windowAspect;
	if (withResolution) {
		block.resolutionWidth = width;
		block.resolutionHeight = height;
	}

	V3 right, top, dir, eye;
	if (p.perspective) {
		eye = {p.eye[0], p.eye[1], p.eye[2]};
		const V3 target {p.target[0], p.target[1], p.target[2]};
		dir = Norm (Sub (target, eye));
		if (Len (dir) < 0.5)
			return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Standort und Ziel der Kamera fallen zusammen.");
		if (!(p.viewConeDegrees > 0.0 && p.viewConeDegrees < 180.0))
			return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Der Öffnungswinkel der Kamera ist unbrauchbar.");

		// Zweifluchtpunkt: Bildebene senkrecht, also waagerechte Blickrichtung; die Neigung wandert in
		// einen senkrechten Shift (Q-09).
		double pitchTan = 0.0;
		if (p.twoPoint) {
			const double horizontal = std::sqrt (dir.x * dir.x + dir.y * dir.y);
			if (horizontal > 1e-9) {
				pitchTan = dir.z / horizontal;
				dir = Norm (V3 {dir.x, dir.y, 0});
			}
		}

		V3 right0 = Norm (V3 {dir.y, -dir.x, 0});
		if (Len (right0) < 0.5) right0 = {1, 0, 0};  // senkrecht nach oben oder unten
		const V3 top0 = Cross (right0, dir);
		const double roll = p.twoPoint ? 0.0 : p.rollDegrees * kPi / 180.0;
		const double s = std::sin (roll), c = std::cos (roll);
		right = Add (Scale (right0, c), Scale (top0, s));
		top = Sub (Scale (top0, c), Scale (right0, s));

		// `viewCone` ist waagerecht über die Breite des Fensters (Q-01).
		const double windowHalfTanH = std::tan (p.viewConeDegrees * kPi / 180.0 / 2);
		const double halfTanV = keepsHeight ? windowHalfTanH / windowAspect : windowHalfTanH / aspect;
		const double halfTanH = halfTanV * aspect;
		cam.perspective = true;
		cam.yfov = 2 * std::atan (halfTanV);
		cam.znear = kPerspectiveNear;
		block.projection = "perspective";
		block.fovAxis = "horizontal";
		block.fovAngle = 2 * std::atan (halfTanH);
		block.clipNear = kPerspectiveNear;
		block.clipFar = 0.0;

		if (p.twoPoint && std::fabs (pitchTan) > 1e-9) {
			// Lage des Ziels auf der senkrechten Bildebene in NDC: tan(Neigung) / tan(yfov/2). Shift ist der
			// Anteil der längeren Bildseite (gltf-camera-extras).
			const double yNdc = pitchTan / halfTanV;
			double shiftY = RatioCanon ((yNdc / 2.0) * (aspect >= 1 ? 1.0 / aspect : 1.0));
			if (std::fabs (shiftY) > 2.0) {
				shiftY = shiftY > 0 ? 2.0 : -2.0;
				out.fidelity = "approximated";
				out.note = "Shift über 2 auf 2 begrenzt";
			} else {
				out.note = "Zweifluchtpunkt als waagerechte Kamera mit senkrechtem Shift";
			}
			block.shiftY = shiftY;
			if (shiftY != 0.0)
				cam.extrasJson = "{\"rendertaxi\":{\"camera\":{\"shift\":{\"x\":0,\"y\":" + Number (shiftY) + "}}}}";
		}
	} else {
		// Parallelprojektion: `tranmat` bildet Modell- auf Projektionskoordinaten ab.
		const double* t = p.tranmat;
		const V3 rowX {t[0], t[1], t[2]};
		const V3 rowY {t[4], t[5], t[6]};
		const double lenX = Len (rowX), lenY = Len (rowY);
		if (lenX < 1e-9 || lenY < 1e-9)
			return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Die Parallelprojektion nennt keine Bildachsen.");
		right = Norm (rowX);
		const V3 topRaw = Norm (rowY);
		// Schiefe Parallelprojektion (Kavalier, Frontal): Bildachsen nicht senkrecht — glTF kennt nur die
		// rechtwinklige. `oben` wird gegen `rechts` begradigt, damit die Drehung eine Drehung bleibt.
		const double shear = Dot (right, topRaw);
		top = Norm (Sub (topRaw, Scale (right, shear)));
		if (Len (top) < 0.5)
			return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Die Parallelprojektion nennt keine Bildachsen.");
		if (std::fabs (shear) > 1e-3) {
			out.fidelity = "approximated";
			char degrees[16];
			std::snprintf (degrees, sizeof (degrees), "%.1f", std::asin (std::min (1.0, std::fabs (shear))) * 180 / kPi);
			out.note = std::string ("Schiefe Parallelprojektion (Scherung ") + degrees + "°), als rechtwinklige genähert";
		}
		// Rücken der Kamera: rechts × oben (rechtshändig); die Kamera blickt entgegen.
		const V3 back = Cross (right, top);
		dir = Scale (back, -1.0);

		// Ausschnitt (gemessen am Bild, `model-glb-spike.md` Frage 5): Pixel des 3D-Fensters
		//   x = zoomDispX + zoomScaleX · u,   y = −(zoomDispY + zoomScaleY · v),   (u, v) = tranmat · p.
		// Die Zeilenlängen sind die Verkürzung der Axonometrie je Bildachse.
		double ymag = 0, xmag = 0;
		V3 centre {};
		if (p.zoomScaleX > 0 && p.zoomScaleY > 0 && p.hSize > 0 && p.vSize > 0) {
			ymag = (p.vSize / 2.0) / (p.zoomScaleY * lenY);
			xmag = (p.hSize / 2.0) / (p.zoomScaleX * lenX);
			const double uc = (p.hSize / 2.0 - p.zoomDispX) / p.zoomScaleX - t[3];
			const double vc = (-p.vSize / 2.0 - p.zoomDispY) / p.zoomScaleY - t[7];
			centre = Add (Scale (right, uc / lenX), Scale (top, vc / lenY));
		} else if (scene.known) {
			// Ohne Ausschnitt des Fensters: die ganze Szene im Bild.
			centre = {(scene.min[0] + scene.max[0]) / 2, (scene.min[1] + scene.max[1]) / 2,
					  (scene.min[2] + scene.max[2]) / 2};
			double halfW = 0, halfH = 0;
			for (int i = 0; i < 8; ++i) {
				const V3 corner {(i & 1) ? scene.max[0] : scene.min[0], (i & 2) ? scene.max[1] : scene.min[1],
								 (i & 4) ? scene.max[2] : scene.min[2]};
				halfW = std::max (halfW, std::fabs (Dot (Sub (corner, centre), right)));
				halfH = std::max (halfH, std::fabs (Dot (Sub (corner, centre), top)));
			}
			ymag = std::max ({halfH, halfW / aspect, 0.5}) * 1.05;
			xmag = ymag * aspect;
			out.fidelity = "approximated";
			out.note = "Parallelprojektion ohne Ausschnitt des 3D-Fensters";
		} else {
			return Result<MappedCamera>::Fail (errc::SchemaInvalid, "Die Parallelprojektion nennt keinen Ausschnitt.");
		}
		// Der mittige Zuschnitt in Pixeln, nicht in Metern: bei ungleicher Verkürzung ist xmag / ymag
		// nicht das Seitenverhältnis des Fensters (QA-11).
		if (p.hSize > 0 && p.vSize > 0 && p.zoomScaleX > 0 && p.zoomScaleY > 0) {
			if (keepsHeight)
				xmag *= aspect / windowAspect;
			else
				ymag *= windowAspect / aspect;
		}

		// Nah und fern aus der Hülle (F-01 an PR #303): Standort zuerst in der Bildmitte, dann so weit
		// zurück, dass die nächste Ecke mindestens nah plus Abstand vor der Kamera liegt.
		eye = centre;
		double nearest = 0, farthest = 2000;
		if (scene.known) {
			SceneDepths (scene, eye, dir, nearest, farthest);
		} else {
			nearest = -1000;
			farthest = 1000;
		}
		const double margin = std::max (kParallelNear, kDepthMargin * (farthest - nearest));
		const double backOff = std::max (0.0, kParallelNear + margin - nearest);
		eye = Sub (eye, Scale (dir, backOff));
		const double zfar = farthest + backOff + margin;

		cam.perspective = false;
		cam.xmag = xmag;
		cam.ymag = ymag;
		cam.znear = kParallelNear;
		cam.zfar = zfar;
		block.projection = "orthographic";
		block.halfWidth = xmag;
		block.halfHeight = ymag;
		block.clipNear = kParallelNear;
		block.clipFar = zfar;
	}

	const V3 gr = ToGltf (right), gu = ToGltf (top), gb = ToGltf (Scale (dir, -1.0));
	Quaternion (gr, gu, gb, cam.rotation);
	Put (cam.translation, ToGltf (eye));
	Put (block.position, ToGltf (eye));
	Put (block.direction, ToGltf (dir));
	Put (block.up, gu);
	cam.nodeExtrasJson = "{\"rendertaxi\":{\"source\":" + JsonQuote (source) + ",\"fidelity\":" +
						 JsonQuote (out.fidelity) + "}}";
	return Result<MappedCamera>::Ok (out);
}

} // namespace rtx
