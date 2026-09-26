#include "rtx/Ids.hpp"

#include <chrono>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <random>

namespace rtx {
namespace {

const char* kHex = "0123456789abcdef";

} // namespace

void FillRandom (std::uint8_t* out, std::size_t length)
{
	static thread_local std::random_device device;
	for (std::size_t i = 0; i < length; ++i)
		out[i] = static_cast<std::uint8_t> (device () & 0xFFu);
}

std::string RandomHex (std::size_t bytes)
{
	std::string out;
	out.reserve (bytes * 2);
	for (std::size_t i = 0; i < bytes; ++i) {
		std::uint8_t byte = 0;
		FillRandom (&byte, 1);
		out += kHex[byte >> 4];
		out += kHex[byte & 0x0Fu];
	}
	return out;
}

std::uint64_t UnixMillis ()
{
	using namespace std::chrono;
	return static_cast<std::uint64_t> (
		duration_cast<milliseconds> (system_clock::now ().time_since_epoch ()).count ());
}

std::string MakeUuidV7 (std::uint64_t unixMillis, const std::uint8_t random[10])
{
	std::uint8_t bytes[16];
	for (int i = 0; i < 6; ++i)
		bytes[i] = static_cast<std::uint8_t> (unixMillis >> (40 - i * 8));
	std::memcpy (bytes + 6, random, 10);
	bytes[6] = static_cast<std::uint8_t> (0x70u | (bytes[6] & 0x0Fu));   // Version 7
	bytes[8] = static_cast<std::uint8_t> (0x80u | (bytes[8] & 0x3Fu));   // Variante RFC 4122

	std::string out;
	out.reserve (36);
	for (int i = 0; i < 16; ++i) {
		if (i == 4 || i == 6 || i == 8 || i == 10) out += '-';
		out += kHex[bytes[i] >> 4];
		out += kHex[bytes[i] & 0x0Fu];
	}
	return out;
}

std::string NewUuidV7 ()
{
	std::uint8_t random[10];
	FillRandom (random, sizeof random);
	return MakeUuidV7 (UnixMillis (), random);
}

bool IsUuidV7 (const std::string& value)
{
	if (value.size () != 36) return false;
	for (std::size_t i = 0; i < value.size (); ++i) {
		const char c = value[i];
		if (i == 8 || i == 13 || i == 18 || i == 23) {
			if (c != '-') return false;
			continue;
		}
		const bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
		if (!hex) return false;
	}
	if (value[14] != '7') return false;
	const char variant = value[19];
	return variant == '8' || variant == '9' || variant == 'a' || variant == 'b';
}

std::string FormatTimestampUtc (std::uint64_t unixMillis)
{
	const std::time_t seconds = static_cast<std::time_t> (unixMillis / 1000);
	const unsigned millis = static_cast<unsigned> (unixMillis % 1000);
	std::tm utc {};
#if defined (_WIN32)
	gmtime_s (&utc, &seconds);
#else
	gmtime_r (&seconds, &utc);
#endif
	char out[40];
	std::snprintf (out, sizeof out, "%04d-%02d-%02dT%02d:%02d:%02d.%03uZ", utc.tm_year + 1900,
				   utc.tm_mon + 1, utc.tm_mday, utc.tm_hour, utc.tm_min, utc.tm_sec, millis);
	return std::string (out);
}

std::string NowTimestampUtc ()
{
	return FormatTimestampUtc (UnixMillis ());
}

bool IsTimestampUtc (const std::string& value)
{
	if (value.size () != 24) return false;
	static const char* pattern = "####-##-##T##:##:##.###Z";
	for (std::size_t i = 0; i < value.size (); ++i) {
		const char expected = pattern[i];
		const char actual = value[i];
		if (expected == '#') {
			if (actual < '0' || actual > '9') return false;
		} else if (actual != expected) {
			return false;
		}
	}
	return true;
}

} // namespace rtx
