#include "rtx/Sha256.hpp"

#include <cstdio>
#include <cstring>

namespace rtx {
namespace {

constexpr std::uint32_t K[64] = {
	0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u,
	0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu,
	0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu,
	0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
	0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
	0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
	0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u,
	0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
	0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u,
	0xc67178f2u};

inline std::uint32_t Ror (std::uint32_t value, std::uint32_t bits)
{
	return (value >> bits) | (value << (32 - bits));
}

} // namespace

Sha256::Sha256 () : bitCount (0), bufferLength (0), finished (false)
{
	state[0] = 0x6a09e667u;
	state[1] = 0xbb67ae85u;
	state[2] = 0x3c6ef372u;
	state[3] = 0xa54ff53au;
	state[4] = 0x510e527fu;
	state[5] = 0x9b05688cu;
	state[6] = 0x1f83d9abu;
	state[7] = 0x5be0cd19u;
	std::memset (buffer, 0, sizeof buffer);
}

void Sha256::Compress (const std::uint8_t block[64])
{
	std::uint32_t w[64];
	for (int i = 0; i < 16; ++i) {
		w[i] = (static_cast<std::uint32_t> (block[i * 4]) << 24) |
			   (static_cast<std::uint32_t> (block[i * 4 + 1]) << 16) |
			   (static_cast<std::uint32_t> (block[i * 4 + 2]) << 8) |
			   static_cast<std::uint32_t> (block[i * 4 + 3]);
	}
	for (int i = 16; i < 64; ++i) {
		const std::uint32_t s0 = Ror (w[i - 15], 7) ^ Ror (w[i - 15], 18) ^ (w[i - 15] >> 3);
		const std::uint32_t s1 = Ror (w[i - 2], 17) ^ Ror (w[i - 2], 19) ^ (w[i - 2] >> 10);
		w[i] = w[i - 16] + s0 + w[i - 7] + s1;
	}

	std::uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
	std::uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
	for (int i = 0; i < 64; ++i) {
		const std::uint32_t s1 = Ror (e, 6) ^ Ror (e, 11) ^ Ror (e, 25);
		const std::uint32_t ch = (e & f) ^ (~e & g);
		const std::uint32_t t1 = h + s1 + ch + K[i] + w[i];
		const std::uint32_t s0 = Ror (a, 2) ^ Ror (a, 13) ^ Ror (a, 22);
		const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
		const std::uint32_t t2 = s0 + maj;
		h = g; g = f; f = e; e = d + t1;
		d = c; c = b; b = a; a = t1 + t2;
	}
	state[0] += a; state[1] += b; state[2] += c; state[3] += d;
	state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

void Sha256::Update (const void* data, std::size_t length)
{
	if (finished) return;
	const std::uint8_t* bytes = static_cast<const std::uint8_t*> (data);
	bitCount += static_cast<std::uint64_t> (length) * 8;
	while (length > 0) {
		const std::size_t take = (64 - bufferLength < length) ? 64 - bufferLength : length;
		std::memcpy (buffer + bufferLength, bytes, take);
		bufferLength += take;
		bytes += take;
		length -= take;
		if (bufferLength == 64) {
			Compress (buffer);
			bufferLength = 0;
		}
	}
}

void Sha256::Update (const std::string& text)
{
	Update (text.data (), text.size ());
}

std::string Sha256::Hex ()
{
	if (!finished) {
		std::uint8_t pad[72];
		std::memset (pad, 0, sizeof pad);
		pad[0] = 0x80;
		const std::size_t rest = bufferLength % 64;
		const std::size_t padLength = (rest < 56) ? 56 - rest : 120 - rest;
		const std::uint64_t bits = bitCount;
		Update (pad, padLength);
		std::uint8_t tail[8];
		for (int i = 0; i < 8; ++i) tail[i] = static_cast<std::uint8_t> (bits >> (56 - i * 8));
		Update (tail, 8);

		char out[65];
		for (int i = 0; i < 8; ++i) std::snprintf (out + i * 8, 9, "%08x", state[i]);
		digest.assign (out, 64);
		finished = true;
	}
	return digest;
}

std::string Sha256::OfString (const std::string& text)
{
	Sha256 hash;
	hash.Update (text);
	return hash.Hex ();
}

std::string Sha256OfFile (const std::string& path, bool* ok)
{
	if (ok != nullptr) *ok = false;
	std::FILE* file = std::fopen (path.c_str (), "rb");
	if (file == nullptr) return {};
	Sha256 hash;
	std::uint8_t chunk[65536];
	std::size_t read = 0;
	while ((read = std::fread (chunk, 1, sizeof chunk, file)) > 0) hash.Update (chunk, read);
	const bool failed = std::ferror (file) != 0;
	std::fclose (file);
	if (failed) return {};
	if (ok != nullptr) *ok = true;
	return hash.Hex ();
}

} // namespace rtx
