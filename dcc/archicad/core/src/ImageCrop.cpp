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

#elif defined (_WIN32)

// Windows: Windows Imaging Component (WIC). Dieselben Schritte wie ImageIO
// unter macOS — Quelle dekodieren, optional mittig beschneiden, als PNG
// schreiben — und dieselbe Rechnung (`PlanCrop`). Geschrieben wird immer 8 Bit
// je Kanal mit Alphakanal (`32bppBGRA`, im PNG als RGBA), wie unter macOS
// gemessen (`capabilities.md`, Farbtiefe).

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincodec.h>

#include <cstdio>

#include "rtx/Platform.hpp"

namespace rtx {
namespace {

/** Hält eine COM-Referenz und gibt sie am Blockende frei. */
template <typename T>
class ComHolder final {
public:
	ComHolder () = default;
	~ComHolder () { if (held != nullptr) held->Release (); }
	ComHolder (const ComHolder&) = delete;
	ComHolder& operator= (const ComHolder&) = delete;
	T* Get () const { return held; }
	T** Put () { return &held; }
	T* operator-> () const { return held; }
	explicit operator bool () const { return held != nullptr; }

private:
	T* held = nullptr;
};

/**
 * COM für die Dauer eines Aufrufs. Archicad hat COM im Hauptfaden meist schon
 * geöffnet (dann `S_FALSE` oder `RPC_E_CHANGED_MODE`); abgemeldet wird nur,
 * was hier angemeldet wurde.
 */
class ComScope final {
public:
	ComScope () : result (CoInitializeEx (nullptr, COINIT_MULTITHREADED)) {}
	~ComScope () { if (SUCCEEDED (result)) CoUninitialize (); }
	ComScope (const ComScope&) = delete;
	ComScope& operator= (const ComScope&) = delete;
	bool Usable () const { return SUCCEEDED (result) || result == RPC_E_CHANGED_MODE; }

private:
	HRESULT result;
};

struct Decoded {
	ComHolder<IWICImagingFactory> factory;
	ComHolder<IWICBitmapDecoder> decoder;
	ComHolder<IWICBitmapFrameDecode> frame;
	UINT width = 0;
	UINT height = 0;
};

std::string Message (const char* text, HRESULT hr)
{
	char code[16];
	std::snprintf (code, sizeof code, "0x%08lX", static_cast<unsigned long> (hr));
	return std::string (text) + " (" + code + ")";
}

HRESULT Decode (const std::string& sourcePath, Decoded& out)
{
	HRESULT hr = CoCreateInstance (CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
								   IID_PPV_ARGS (out.factory.Put ()));
	if (FAILED (hr)) return hr;
	const std::wstring path = Widen (sourcePath);
	if (path.empty ()) return E_INVALIDARG;
	hr = out.factory->CreateDecoderFromFilename (path.c_str (), nullptr, GENERIC_READ,
												 WICDecodeMetadataCacheOnDemand,
												 out.decoder.Put ());
	if (FAILED (hr)) return hr;
	hr = out.decoder->GetFrame (0, out.frame.Put ());
	if (FAILED (hr)) return hr;
	return out.frame->GetSize (&out.width, &out.height);
}

/** Schreibt `source` (ganz oder `rect`) als PNG nach `targetPath`. */
HRESULT WritePng (IWICImagingFactory* factory, IWICBitmapSource* source, const WICRect* rect,
				  const std::string& targetPath)
{
	ComHolder<IWICBitmapClipper> clipper;
	IWICBitmapSource* input = source;
	HRESULT hr = S_OK;
	if (rect != nullptr) {
		hr = factory->CreateBitmapClipper (clipper.Put ());
		if (FAILED (hr)) return hr;
		hr = clipper->Initialize (source, rect);
		if (FAILED (hr)) return hr;
		input = clipper.Get ();
	}

	ComHolder<IWICFormatConverter> converter;
	hr = factory->CreateFormatConverter (converter.Put ());
	if (FAILED (hr)) return hr;
	hr = converter->Initialize (input, GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
								nullptr, 0.0, WICBitmapPaletteTypeCustom);
	if (FAILED (hr)) return hr;

	UINT width = 0;
	UINT height = 0;
	hr = converter->GetSize (&width, &height);
	if (FAILED (hr)) return hr;

	const std::wstring path = Widen (targetPath);
	if (path.empty ()) return E_INVALIDARG;
	ComHolder<IWICStream> stream;
	hr = factory->CreateStream (stream.Put ());
	if (FAILED (hr)) return hr;
	hr = stream->InitializeFromFilename (path.c_str (), GENERIC_WRITE);
	if (FAILED (hr)) return hr;

	ComHolder<IWICBitmapEncoder> encoder;
	hr = factory->CreateEncoder (GUID_ContainerFormatPng, nullptr, encoder.Put ());
	if (FAILED (hr)) return hr;
	hr = encoder->Initialize (stream.Get (), WICBitmapEncoderNoCache);
	if (FAILED (hr)) return hr;

	ComHolder<IWICBitmapFrameEncode> frame;
	hr = encoder->CreateNewFrame (frame.Put (), nullptr);
	if (FAILED (hr)) return hr;
	hr = frame->Initialize (nullptr);
	if (FAILED (hr)) return hr;
	hr = frame->SetSize (width, height);
	if (FAILED (hr)) return hr;
	WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
	hr = frame->SetPixelFormat (&format);
	if (FAILED (hr)) return hr;
	hr = frame->WriteSource (converter.Get (), nullptr);
	if (FAILED (hr)) return hr;
	hr = frame->Commit ();
	if (FAILED (hr)) return hr;
	return encoder->Commit ();
}

} // namespace

Status ConvertImageToPng (const std::string& sourcePath, const std::string& targetPath)
{
	ComScope com;
	if (!com.Usable ()) return Status::Fail (errc::IoFailed, "COM ist nicht verfügbar.");
	Decoded decoded;
	HRESULT hr = Decode (sourcePath, decoded);
	if (FAILED (hr))
		return Status::Fail (errc::IoFailed, Message ("Das gerenderte Bild ließ sich nicht lesen.", hr));
	if (decoded.width == 0 || decoded.height == 0)
		return Status::Fail (errc::IoFailed, "Das gerenderte Bild ist leer.");
	hr = WritePng (decoded.factory.Get (), decoded.frame.Get (), nullptr, targetPath);
	if (FAILED (hr))
		return Status::Fail (errc::IoFailed, Message ("PNG ließ sich nicht schreiben.", hr));
	return Status::Ok ();
}

Result<CropResult> CropImageToAspect (const std::string& sourcePath,
									  const std::string& targetPath, int aspectWidth,
									  int aspectHeight)
{
	CropResult result;
	if (aspectWidth <= 0 || aspectHeight <= 0) return Result<CropResult>::Ok (result);

	ComScope com;
	if (!com.Usable ())
		return Result<CropResult>::Fail (errc::IoFailed, "COM ist nicht verfügbar.");
	Decoded decoded;
	HRESULT hr = Decode (sourcePath, decoded);
	if (FAILED (hr))
		return Result<CropResult>::Fail (errc::IoFailed,
										 Message (("Bild ließ sich nicht lesen: " + sourcePath).c_str (), hr));
	if (decoded.width == 0 || decoded.height == 0)
		return Result<CropResult>::Fail (errc::IoFailed, "Bild ohne gültige Maße.");
	result.width = static_cast<int> (decoded.width);
	result.height = static_cast<int> (decoded.height);

	const CropPlan plan = PlanCrop (result.width, result.height, aspectWidth, aspectHeight);
	if (!plan.needed) return Result<CropResult>::Ok (result);

	const WICRect rect = {plan.x, plan.y, plan.width, plan.height};
	hr = WritePng (decoded.factory.Get (), decoded.frame.Get (), &rect, targetPath);
	if (FAILED (hr))
		return Result<CropResult>::Fail (errc::IoFailed,
										 Message (("PNG ließ sich nicht schreiben: " + targetPath).c_str (), hr));

	result.cropped = true;
	result.width = plan.width;
	result.height = plan.height;
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
	// Ohne Zuschnitt würde ein Update das Format des Blickpunkts verfehlen —
	// das wäre schlimmer als ein Fehler.
	return Result<CropResult>::Fail (errc::IoFailed,
									 "Zuschnitt ist auf dieser Plattform nicht umgesetzt.");
}

} // namespace rtx

#endif
