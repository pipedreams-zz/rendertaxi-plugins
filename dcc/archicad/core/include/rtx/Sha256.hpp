// SHA-256 ohne externe Abhängigkeit.
//
// Der Kern hasht drei Dinge: die Bytes einer Bilddatei (Assethash), die
// kanonischen Zeilen des `contentHash` und die Rohbytes des Manifests. Alle
// drei müssen bitgleich zu `integrations/_shared/tools/canonical-hash.mjs`
// sein, weshalb hier eine eigene, prüfbare Umsetzung steht statt eines
// Betriebssystemdienstes.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace rtx {

class Sha256 final {
public:
	Sha256 ();

	void Update (const void* data, std::size_t length);
	void Update (const std::string& text);

	/** Schließt den Hash ab und gibt 64 Hexziffern in Kleinschreibung zurück. */
	std::string Hex ();

	static std::string OfString (const std::string& text);

private:
	void Compress (const std::uint8_t block[64]);

	std::uint32_t state[8];
	std::uint64_t bitCount;
	std::uint8_t buffer[64];
	std::size_t bufferLength;
	bool finished;
	std::string digest;
};

/** SHA-256 über den Inhalt einer Datei; leer, wenn sie nicht lesbar ist. */
std::string Sha256OfFile (const std::string& path, bool* ok = nullptr);

} // namespace rtx
