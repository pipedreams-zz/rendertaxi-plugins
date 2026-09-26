#include "rtx/ImageFile.hpp"

#include <cstdio>
#include <cstring>
#include <vector>

#include "rtx/Platform.hpp"

namespace rtx {
namespace {

Result<ImageInfo> Fail (const std::string& message)
{
	return Result<ImageInfo>::Fail (errc::IoFailed, message);
}

std::uint32_t BigEndian32 (const std::uint8_t* p)
{
	return (static_cast<std::uint32_t> (p[0]) << 24) | (static_cast<std::uint32_t> (p[1]) << 16) |
		   (static_cast<std::uint32_t> (p[2]) << 8) | static_cast<std::uint32_t> (p[3]);
}

Result<ImageInfo> ReadPng (const std::vector<std::uint8_t>& bytes)
{
	if (bytes.size () < 33) return Fail ("PNG-Datei zu kurz für den IHDR-Block.");
	if (std::memcmp (bytes.data () + 12, "IHDR", 4) != 0) return Fail ("PNG ohne führenden IHDR-Block.");

	ImageInfo info;
	info.mediaType = "image/png";
	info.width = static_cast<int> (BigEndian32 (bytes.data () + 16));
	info.height = static_cast<int> (BigEndian32 (bytes.data () + 20));
	info.bitDepth = bytes[24];
	const std::uint8_t colorType = bytes[25];
	switch (colorType) {
		case 0: info.channels = "gray"; break;
		case 2: info.channels = "rgb"; break;
		case 3: info.channels = "rgb"; info.bitDepth = 8; break;   // Palette, dekodiert 8 Bit RGB
		case 4: info.channels = "gray-alpha"; break;
		case 6: info.channels = "rgba"; break;
		default: return Fail ("PNG mit unbekanntem Farbtyp.");
	}
	if (info.width <= 0 || info.height <= 0) return Fail ("PNG ohne gültige Maße.");
	return Result<ImageInfo>::Ok (info);
}

Result<ImageInfo> ReadJpeg (const std::vector<std::uint8_t>& bytes)
{
	std::size_t pos = 2;
	while (pos + 9 < bytes.size ()) {
		if (bytes[pos] != 0xFF) { ++pos; continue; }
		const std::uint8_t marker = bytes[pos + 1];
		if (marker == 0xD8 || marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) {
			pos += 2;
			continue;
		}
		const std::size_t length =
			(static_cast<std::size_t> (bytes[pos + 2]) << 8) | bytes[pos + 3];
		const bool isStartOfFrame =
			(marker >= 0xC0 && marker <= 0xCF) && marker != 0xC4 && marker != 0xC8 && marker != 0xCC;
		if (isStartOfFrame) {
			ImageInfo info;
			info.mediaType = "image/jpeg";
			info.bitDepth = bytes[pos + 4];
			info.height = (static_cast<int> (bytes[pos + 5]) << 8) | bytes[pos + 6];
			info.width = (static_cast<int> (bytes[pos + 7]) << 8) | bytes[pos + 8];
			const std::uint8_t components = bytes[pos + 9];
			info.channels = components == 1 ? "gray" : "rgb";
			if (info.width <= 0 || info.height <= 0) return Fail ("JPEG ohne gültige Maße.");
			return Result<ImageInfo>::Ok (info);
		}
		pos += 2 + length;
	}
	return Fail ("JPEG ohne lesbaren Rahmenkopf.");
}

} // namespace

Result<ImageInfo> ReadImageInfo (const std::string& path)
{
	std::FILE* file = OpenFile (path, "rb");
	if (file == nullptr) return Fail ("Bilddatei nicht lesbar: " + path);
	std::vector<std::uint8_t> head (4096);
	const std::size_t read = std::fread (head.data (), 1, head.size (), file);
	std::fclose (file);
	head.resize (read);

	static const std::uint8_t pngSignature[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
	if (head.size () >= 8 && std::memcmp (head.data (), pngSignature, 8) == 0) return ReadPng (head);
	if (head.size () >= 3 && head[0] == 0xFF && head[1] == 0xD8 && head[2] == 0xFF)
		return ReadJpeg (head);
	return Fail ("Unbekanntes Bildformat: " + path);
}

} // namespace rtx
