#include "rtx/ImageCrop.hpp"

#if defined (__APPLE__)

#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>

#include <cmath>

namespace rtx {
namespace {

/** Hält eine CoreFoundation-Referenz und gibt sie am Blockende frei. */
template <typename T>
class CFHolder final {
public:
	explicit CFHolder (T value) : held (value) {}
	~CFHolder () { if (held != nullptr) CFRelease (held); }
	T Get () const { return held; }
	explicit operator bool () const { return held != nullptr; }

	CFHolder (const CFHolder&) = delete;
	CFHolder& operator= (const CFHolder&) = delete;

private:
	T held;
};

Result<CropResult> Fail (const std::string& message)
{
	return Result<CropResult>::Fail (errc::IoFailed, message);
}

CFURLRef MakeFileUrl (const std::string& path)
{
	CFStringRef text = CFStringCreateWithBytes (
		kCFAllocatorDefault, reinterpret_cast<const UInt8*> (path.data ()),
		static_cast<CFIndex> (path.size ()), kCFStringEncodingUTF8, false);
	if (text == nullptr) return nullptr;
	CFURLRef url = CFURLCreateWithFileSystemPath (kCFAllocatorDefault, text,
												  kCFURLPOSIXPathStyle, false);
	CFRelease (text);
	return url;
}

} // namespace

Status ConvertImageToPng (const std::string& sourcePath, const std::string& targetPath)
{
	CFHolder<CFURLRef> sourceUrl (MakeFileUrl (sourcePath));
	if (!sourceUrl) return Status::Fail (errc::IoFailed, "Quellpfad ließ sich nicht auflösen.");
	CFHolder<CGImageSourceRef> source (
		CGImageSourceCreateWithURL (sourceUrl.Get (), nullptr));
	if (!source) return Status::Fail (errc::IoFailed, "Das gerenderte Bild ließ sich nicht lesen.");
	CFHolder<CGImageRef> image (CGImageSourceCreateImageAtIndex (source.Get (), 0, nullptr));
	if (!image) return Status::Fail (errc::IoFailed, "Das gerenderte Bild ist leer.");

	CFHolder<CFURLRef> targetUrl (MakeFileUrl (targetPath));
	if (!targetUrl) return Status::Fail (errc::IoFailed, "Zielpfad ließ sich nicht auflösen.");
	CFHolder<CGImageDestinationRef> destination (
		CGImageDestinationCreateWithURL (targetUrl.Get (), CFSTR ("public.png"), 1, nullptr));
	if (!destination) return Status::Fail (errc::IoFailed, "PNG ließ sich nicht anlegen.");
	CGImageDestinationAddImage (destination.Get (), image.Get (), nullptr);
	if (!CGImageDestinationFinalize (destination.Get ()))
		return Status::Fail (errc::IoFailed, "PNG ließ sich nicht schreiben.");
	return Status::Ok ();
}

Result<CropResult> CropImageToAspect (const std::string& sourcePath,
									  const std::string& targetPath, int aspectWidth,
									  int aspectHeight)
{
	CropResult result;
	if (aspectWidth <= 0 || aspectHeight <= 0) return Result<CropResult>::Ok (result);

	CFHolder<CFURLRef> sourceUrl (MakeFileUrl (sourcePath));
	if (!sourceUrl) return Fail ("Quellpfad ließ sich nicht auflösen.");

	CFHolder<CGImageSourceRef> source (
		CGImageSourceCreateWithURL (sourceUrl.Get (), nullptr));
	if (!source) return Fail ("Bild ließ sich nicht lesen: " + sourcePath);

	CFHolder<CGImageRef> image (
		CGImageSourceCreateImageAtIndex (source.Get (), 0, nullptr));
	if (!image) return Fail ("Bild ließ sich nicht dekodieren: " + sourcePath);

	const int width = static_cast<int> (CGImageGetWidth (image.Get ()));
	const int height = static_cast<int> (CGImageGetHeight (image.Get ()));
	if (width <= 0 || height <= 0) return Fail ("Bild ohne gültige Maße.");
	result.width = width;
	result.height = height;

	const double wanted =
		static_cast<double> (aspectWidth) / static_cast<double> (aspectHeight);
	const double actual = static_cast<double> (width) / static_cast<double> (height);
	// Ein halbes Prozent Abweichung ist keinen Zuschnitt wert: das Rezept
	// beschreibt ein Verhältnis, keine Pixelgrenze.
	if (std::fabs (actual - wanted) / wanted < 0.005) return Result<CropResult>::Ok (result);

	int cropWidth = width;
	int cropHeight = height;
	if (actual > wanted) {
		// Zu breit: links und rechts mittig beschneiden.
		cropWidth = static_cast<int> (static_cast<double> (height) * wanted + 0.5);
		if (cropWidth > width) cropWidth = width;
	} else {
		// Zu hoch: oben und unten mittig beschneiden — der übliche Fall, wenn
		// ein fast quadratisches 3D-Fenster in ein Breitformat soll.
		cropHeight = static_cast<int> (static_cast<double> (width) / wanted + 0.5);
		if (cropHeight > height) cropHeight = height;
	}
	if (cropWidth < 1 || cropHeight < 1) return Fail ("Zuschnitt ergäbe ein leeres Bild.");

	// Ganzzahlige Ränder: `CGImageCreateWithImageInRect` rundet ein Rechteck mit
	// gebrochenem Ursprung nach außen auf ganze Pixel und liefert dann mehr
	// Zeilen, als verlangt waren.
	const int offsetX = (width - cropWidth) / 2;
	const int offsetY = (height - cropHeight) / 2;
	const CGRect rect = CGRectMake (static_cast<CGFloat> (offsetX), static_cast<CGFloat> (offsetY),
									static_cast<CGFloat> (cropWidth),
									static_cast<CGFloat> (cropHeight));
	CFHolder<CGImageRef> cropped (CGImageCreateWithImageInRect (image.Get (), rect));
	if (!cropped) return Fail ("Zuschnitt fehlgeschlagen.");

	CFHolder<CFURLRef> targetUrl (MakeFileUrl (targetPath));
	if (!targetUrl) return Fail ("Zielpfad ließ sich nicht auflösen.");

	CFHolder<CGImageDestinationRef> destination (
		CGImageDestinationCreateWithURL (targetUrl.Get (), CFSTR ("public.png"), 1, nullptr));
	if (!destination) return Fail ("PNG-Ziel ließ sich nicht anlegen.");

	CGImageDestinationAddImage (destination.Get (), cropped.Get (), nullptr);
	if (!CGImageDestinationFinalize (destination.Get ()))
		return Fail ("PNG ließ sich nicht schreiben: " + targetPath);

	result.cropped = true;
	result.width = static_cast<int> (CGImageGetWidth (cropped.Get ()));
	result.height = static_cast<int> (CGImageGetHeight (cropped.Get ()));
	return Result<CropResult>::Ok (result);
}

} // namespace rtx

#else

namespace rtx {

Status ConvertImageToPng (const std::string&, const std::string&)
{
	return Status::Fail (errc::IoFailed,
						 "PNG-Umwandlung ist auf dieser Plattform nicht umgesetzt.");
}

Result<CropResult> CropImageToAspect (const std::string&, const std::string&, int, int)
{
	// Windows ist in Issue #20 Nicht-Ziel. Ohne Zuschnitt würde ein Update das
	// Format des Blickpunkts verfehlen — das wäre schlimmer als ein Fehler.
	return Result<CropResult>::Fail (errc::IoFailed,
									 "Zuschnitt ist auf dieser Plattform nicht umgesetzt.");
}

} // namespace rtx

#endif
