#include "rtx/Canonical.hpp"

#include <algorithm>
#include <string>

#include "rtx/Sha256.hpp"

namespace rtx {

Result<std::string> EncodeField (const std::string& value)
{
	for (const char raw : value) {
		if (static_cast<unsigned char> (raw) > 0x7F) {
			return Result<std::string>::Fail (
				errc::NotCanonical,
				"Nicht-ASCII in einem Hashfeld: der Bildweg v1 kennt dafür keine "
				"Normalisierung und rät nicht.");
		}
	}
	return Result<std::string>::Ok (std::to_string (value.size ()) + ":" + value);
}

std::string EncodeAbsent ()
{
	return "-1:";
}

void SortByUtf8 (std::vector<std::string>& values)
{
	// std::string vergleicht byteweise über char. Auf Plattformen mit
	// vorzeichenbehaftetem char wäre das für Bytes ≥ 0x80 nicht die
	// UTF-8-Byteordnung, deshalb wird ausdrücklich unsigned verglichen.
	std::sort (values.begin (), values.end (), [] (const std::string& a, const std::string& b) {
		const std::size_t common = std::min (a.size (), b.size ());
		for (std::size_t i = 0; i < common; ++i) {
			const unsigned char left = static_cast<unsigned char> (a[i]);
			const unsigned char right = static_cast<unsigned char> (b[i]);
			if (left != right) return left < right;
		}
		return a.size () < b.size ();
	});
}

Result<std::string> CaptureContentHash (const std::vector<ContentHashAsset>& present)
{
	std::vector<std::string> lines;
	lines.reserve (present.size ());
	for (const ContentHashAsset& asset : present) {
		const Result<std::string> tag = EncodeField ("asset");
		const Result<std::string> role = EncodeField (asset.role);
		const Result<std::string> path = EncodeField (asset.path);
		const Result<std::string> hash = EncodeField (asset.sha256);
		if (!tag) return tag;
		if (!role) return role;
		if (!path) return path;
		if (!hash) return hash;
		lines.push_back (tag.Value () + "\t" + role.Value () + "\t" + path.Value () + "\t" +
						 hash.Value ());
	}
	SortByUtf8 (lines);

	Sha256 digest;
	for (const std::string& line : lines) {
		digest.Update (line);
		digest.Update ("\n");
	}
	return Result<std::string>::Ok (digest.Hex ());
}

} // namespace rtx
