#include "rtx/TextureImage.hpp"

#include <algorithm>
#include <cmath>

namespace rtx {

RgbaImage DownscaleImage (const RgbaImage& image, int width, int height)
{
	RgbaImage out;
	if (!image.Valid () || width <= 0 || height <= 0) return out;
	width = std::min (width, image.width);
	height = std::min (height, image.height);
	out.width = width;
	out.height = height;
	out.alpha = image.alpha;
	out.rgba.resize (static_cast<std::size_t> (width) * height * 4);
	for (int y = 0; y < height; ++y) {
		const int y0 = static_cast<int> (static_cast<long long> (y) * image.height / height);
		const int y1 = std::max (y0 + 1, static_cast<int> (static_cast<long long> (y + 1) * image.height / height));
		for (int x = 0; x < width; ++x) {
			const int x0 = static_cast<int> (static_cast<long long> (x) * image.width / width);
			const int x1 = std::max (x0 + 1, static_cast<int> (static_cast<long long> (x + 1) * image.width / width));
			unsigned long long sum[4] = {0, 0, 0, 0};
			for (int sy = y0; sy < y1; ++sy)
				for (int sx = x0; sx < x1; ++sx) {
					const std::uint8_t* p = &image.rgba[(static_cast<std::size_t> (sy) * image.width + sx) * 4];
					for (int k = 0; k < 4; ++k) sum[k] += p[k];
				}
			const unsigned long long count = static_cast<unsigned long long> (y1 - y0) * (x1 - x0);
			std::uint8_t* q = &out.rgba[(static_cast<std::size_t> (y) * width + x) * 4];
			for (int k = 0; k < 4; ++k) q[k] = static_cast<std::uint8_t> ((sum[k] + count / 2) / count);
		}
	}
	return out;
}

Result<EncodedImage> TextureForGlb (const RgbaImage& image, std::uint64_t maxBytes, ImageEncoder encode)
{
	if (!image.Valid ()) return Result<EncodedImage>::Fail (errc::IoFailed, "Die Textur hat keine Pixel.");
	Result<EncodedImage> first = encode (image);
	if (!first || first.Value ().bytes.size () <= maxBytes) return first;
	double factor = 1.0;
	double size = static_cast<double> (first.Value ().bytes.size ());
	for (int attempt = 0; attempt < 8; ++attempt) {
		factor *= std::min (0.95, std::max (0.25, std::sqrt (static_cast<double> (maxBytes) / size) * 0.95));
		const int width = std::max (1, static_cast<int> (image.width * factor));
		const int height = std::max (1, static_cast<int> (image.height * factor));
		Result<EncodedImage> next = encode (DownscaleImage (image, width, height));
		if (!next) return next;
		if (next.Value ().bytes.size () <= maxBytes) {
			EncodedImage done = next.Value ();
			done.downscaled = true;
			return Result<EncodedImage>::Ok (std::move (done));
		}
		size = static_cast<double> (next.Value ().bytes.size ());
	}
	return Result<EncodedImage>::Fail (errc::LimitExceeded, "Die Textur ließ sich nicht unter 2 MB bringen.");
}

} // namespace rtx

#if defined (__APPLE__)

#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>

namespace rtx {
namespace {

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

Result<EncodedImage> Fail (const char* message)
{
	return Result<EncodedImage>::Fail (errc::IoFailed, message);
}

} // namespace

Result<EncodedImage> EncodeImage (const RgbaImage& image)
{
	if (!image.Valid ()) return Fail ("Die Textur hat keine Pixel.");
	CFHolder<CGColorSpaceRef> space (CGColorSpaceCreateWithName (kCGColorSpaceSRGB));
	CFHolder<CFDataRef> pixels (CFDataCreate (kCFAllocatorDefault, image.rgba.data (),
											  static_cast<CFIndex> (image.rgba.size ())));
	if (!space || !pixels) return Fail ("Die Textur ließ sich nicht anlegen.");
	CFHolder<CGDataProviderRef> provider (CGDataProviderCreateWithCFData (pixels.Get ()));
	const CGBitmapInfo info = static_cast<CGBitmapInfo> (image.alpha ? kCGImageAlphaLast : kCGImageAlphaNoneSkipLast) |
							  kCGBitmapByteOrderDefault;
	CFHolder<CGImageRef> cg (CGImageCreate (static_cast<size_t> (image.width), static_cast<size_t> (image.height), 8,
											32, static_cast<size_t> (image.width) * 4, space.Get (), info,
											provider.Get (), nullptr, false, kCGRenderingIntentDefault));
	if (!cg) return Fail ("Die Textur ließ sich nicht anlegen.");

	CFHolder<CFMutableDataRef> data (CFDataCreateMutable (kCFAllocatorDefault, 0));
	CFHolder<CGImageDestinationRef> destination (CGImageDestinationCreateWithData (
		data.Get (), image.alpha ? CFSTR ("public.png") : CFSTR ("public.jpeg"), 1, nullptr));
	if (!destination) return Fail ("Die Textur ließ sich nicht kodieren.");
	const float quality = 0.9f;
	CFHolder<CFNumberRef> number (CFNumberCreate (kCFAllocatorDefault, kCFNumberFloatType, &quality));
	const void* keys[] = {kCGImageDestinationLossyCompressionQuality};
	const void* values[] = {number.Get ()};
	CFHolder<CFDictionaryRef> options (CFDictionaryCreate (kCFAllocatorDefault, keys, values, 1,
														   &kCFTypeDictionaryKeyCallBacks,
														   &kCFTypeDictionaryValueCallBacks));
	CGImageDestinationAddImage (destination.Get (), cg.Get (), image.alpha ? nullptr : options.Get ());
	if (!CGImageDestinationFinalize (destination.Get ())) return Fail ("Die Textur ließ sich nicht kodieren.");

	EncodedImage out;
	const UInt8* bytes = CFDataGetBytePtr (data.Get ());
	out.bytes.assign (bytes, bytes + CFDataGetLength (data.Get ()));
	out.mimeType = image.alpha ? "image/png" : "image/jpeg";
	out.width = image.width;
	out.height = image.height;
	return Result<EncodedImage>::Ok (std::move (out));
}

} // namespace rtx

#elif defined (_WIN32)

// Windows: WIC wie der Zuschnitt (`ImageCrop.cpp`), in einen Speicherstrom
// statt in eine Datei. JPEG mit `ImageQuality` 0,9, PNG mit Alphakanal.

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <objbase.h>
#include <ocidl.h>
#include <wincodec.h>

#include <cstring>

namespace rtx {
namespace {

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

HRESULT Encode (const RgbaImage& image, std::vector<std::uint8_t>& out)
{
	ComHolder<IWICImagingFactory> factory;
	HRESULT hr = CoCreateInstance (CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
								   IID_PPV_ARGS (factory.Put ()));
	if (FAILED (hr)) return hr;

	// WIC kennt 32bppBGRA: Rot und Blau tauschen.
	std::vector<std::uint8_t> bgra (image.rgba);
	for (std::size_t i = 0; i + 3 < bgra.size (); i += 4) std::swap (bgra[i], bgra[i + 2]);
	ComHolder<IWICBitmap> bitmap;
	hr = factory->CreateBitmapFromMemory (static_cast<UINT> (image.width), static_cast<UINT> (image.height),
										  GUID_WICPixelFormat32bppBGRA, static_cast<UINT> (image.width) * 4,
										  static_cast<UINT> (bgra.size ()), bgra.data (), bitmap.Put ());
	if (FAILED (hr)) return hr;

	ComHolder<IStream> stream;
	hr = CreateStreamOnHGlobal (nullptr, TRUE, stream.Put ());
	if (FAILED (hr)) return hr;
	ComHolder<IWICBitmapEncoder> encoder;
	hr = factory->CreateEncoder (image.alpha ? GUID_ContainerFormatPng : GUID_ContainerFormatJpeg, nullptr,
								 encoder.Put ());
	if (FAILED (hr)) return hr;
	hr = encoder->Initialize (stream.Get (), WICBitmapEncoderNoCache);
	if (FAILED (hr)) return hr;

	ComHolder<IWICBitmapFrameEncode> frame;
	ComHolder<IPropertyBag2> properties;
	hr = encoder->CreateNewFrame (frame.Put (), properties.Put ());
	if (FAILED (hr)) return hr;
	if (!image.alpha && properties) {
		PROPBAG2 option {};
		option.pstrName = const_cast<LPOLESTR> (L"ImageQuality");
		VARIANT value {};
		value.vt = VT_R4;
		value.fltVal = 0.9f;
		properties->Write (1, &option, &value);
	}
	hr = frame->Initialize (properties.Get ());
	if (FAILED (hr)) return hr;
	hr = frame->SetSize (static_cast<UINT> (image.width), static_cast<UINT> (image.height));
	if (FAILED (hr)) return hr;
	WICPixelFormatGUID format = image.alpha ? GUID_WICPixelFormat32bppBGRA : GUID_WICPixelFormat24bppBGR;
	hr = frame->SetPixelFormat (&format);
	if (FAILED (hr)) return hr;
	ComHolder<IWICFormatConverter> converter;
	hr = factory->CreateFormatConverter (converter.Put ());
	if (FAILED (hr)) return hr;
	hr = converter->Initialize (bitmap.Get (), format, WICBitmapDitherTypeNone, nullptr, 0.0,
								WICBitmapPaletteTypeCustom);
	if (FAILED (hr)) return hr;
	hr = frame->WriteSource (converter.Get (), nullptr);
	if (FAILED (hr)) return hr;
	hr = frame->Commit ();
	if (FAILED (hr)) return hr;
	hr = encoder->Commit ();
	if (FAILED (hr)) return hr;

	STATSTG stat {};
	hr = stream->Stat (&stat, STATFLAG_NONAME);
	if (FAILED (hr)) return hr;
	HGLOBAL global = nullptr;
	hr = GetHGlobalFromStream (stream.Get (), &global);
	if (FAILED (hr)) return hr;
	const void* bytes = GlobalLock (global);
	if (bytes == nullptr) return E_FAIL;
	out.resize (static_cast<std::size_t> (stat.cbSize.QuadPart));
	std::memcpy (out.data (), bytes, out.size ());
	GlobalUnlock (global);
	return S_OK;
}

} // namespace

Result<EncodedImage> EncodeImage (const RgbaImage& image)
{
	if (!image.Valid ()) return Result<EncodedImage>::Fail (errc::IoFailed, "Die Textur hat keine Pixel.");
	ComScope com;
	if (!com.Usable ()) return Result<EncodedImage>::Fail (errc::IoFailed, "COM ist nicht verfügbar.");
	EncodedImage out;
	if (FAILED (Encode (image, out.bytes)))
		return Result<EncodedImage>::Fail (errc::IoFailed, "Die Textur ließ sich nicht kodieren.");
	out.mimeType = image.alpha ? "image/png" : "image/jpeg";
	out.width = image.width;
	out.height = image.height;
	return Result<EncodedImage>::Ok (std::move (out));
}

} // namespace rtx

#else

namespace rtx {

Result<EncodedImage> EncodeImage (const RgbaImage&)
{
	return Result<EncodedImage>::Fail (errc::IoFailed, "Texturen sind auf dieser Plattform nicht umgesetzt.");
}

} // namespace rtx

#endif
