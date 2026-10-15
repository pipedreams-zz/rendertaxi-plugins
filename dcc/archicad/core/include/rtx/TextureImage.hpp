// Texturen für die Modelldatei (RTX-P-021, #526) — die Regel aus
// `integrations/_shared/docs/capture-manifest.md`, Abschnitt 11.6: eine
// Diffustextur je Material, eingebettet, höchstens 2 MB je Bild. Ein größeres
// Bild wird verkleinert, nicht weggelassen.
//
// Die Pixel kommen aus `ModelerAPI::Texture::GetPixelMap` (ARGB); kodiert wird
// mit dem, was das System schon hat — ImageIO unter macOS, WIC unter Windows,
// wie der Zuschnitt (`ImageCrop.hpp`). Keine neue Bibliothek. Das Verkleinern
// (Mittelwert über die Quellpixel) und die Schleife bis unter die Grenze sind
// plattformneutral und ohne Archicad geprüft.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "rtx/Result.hpp"

namespace rtx {

/** Höchstgröße eines Bildes in der Modelldatei (Abschnitt 11.6). */
inline constexpr std::uint64_t kMaxTextureBytes = 2ull * 1024 * 1024;

/** Ein Bild in 8 Bit je Kanal, RGBA, Zeile für Zeile von oben. */
struct RgbaImage {
	int width = 0;
	int height = 0;
	std::vector<std::uint8_t> rgba;
	/** Trägt das Bild einen Alphakanal, der gebraucht wird? Dann PNG, sonst JPEG. */
	bool alpha = false;

	bool Valid () const
	{
		return width > 0 && height > 0 && rgba.size () == static_cast<std::size_t> (width) * height * 4;
	}
};

/** Auf `width × height` verkleinert, jedes Zielpixel der Mittelwert seiner Quellpixel. */
RgbaImage DownscaleImage (const RgbaImage& image, int width, int height);

/** Ein Bild der Datei: kodierte Bytes, Medientyp und Pixelmaß. */
struct EncodedImage {
	std::vector<std::uint8_t> bytes;
	std::string mimeType;
	int width = 0;
	int height = 0;
	/** Größer als die Grenze gewesen und verkleinert. */
	bool downscaled = false;
};

/** Kodiert das Bild als PNG (`alpha`) oder JPEG (Qualität 90) mit den Mitteln des Systems. */
Result<EncodedImage> EncodeImage (const RgbaImage& image);

/**
 * Das Bild für die Modelldatei, höchstens `maxBytes` groß: erst in voller
 * Größe, dann je Versuch um die Wurzel des Größenverhältnisses kleiner (die
 * Bytes wachsen etwa mit der Fläche), höchstens acht Versuche.
 * `encode` ist für Tests ersetzbar; Vorgabe `EncodeImage`.
 */
using ImageEncoder = Result<EncodedImage> (*) (const RgbaImage&);
Result<EncodedImage> TextureForGlb (const RgbaImage& image, std::uint64_t maxBytes = kMaxTextureBytes,
									ImageEncoder encode = &EncodeImage);

} // namespace rtx
