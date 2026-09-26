// HTTP-Port des Kerns.
//
// Die einzige Umsetzung im Add-On ist `CurlHttpClient` über die libcurl des
// Systems — auf macOS Teil des SDK, also keine neu aufgenommene Abhängigkeit im
// Sinne der Reiferichtlinie aus `AGENTS.md`.
//
// Der Port ist abstrakt, damit der Zustandsautomat des Uploaders ohne Netz und
// ohne Archicad prüfbar bleibt. Jeder Aufruf nimmt ein `CancelToken`:
// „Archicad darf während des Uploads nicht blockieren; Abbruch ist möglich."
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "rtx/Result.hpp"

namespace rtx {

using HttpHeaders = std::vector<std::pair<std::string, std::string>>;

struct HttpRequest {
	std::string method = "GET";
	std::string url;
	HttpHeaders headers;
	/** Körper als Text; leer, wenn `bodyFilePath` gesetzt ist. */
	std::string body;
	/** Datei, die als Körper übertragen wird — der bedingte PUT nach ADR 0007. */
	std::string bodyFilePath;
	int timeoutSeconds = 60;
};

struct HttpResponse {
	int status = 0;
	HttpHeaders headers;
	std::string body;

	/** Kopfzeile ohne Rücksicht auf Groß- und Kleinschreibung. */
	std::string Header (const std::string& name) const;
};

/** Von mehreren Fäden gelesenes Abbruchsignal. */
class CancelToken final {
public:
	void Cancel () { cancelled.store (true); }
	bool IsCancelled () const { return cancelled.load (); }
	void Reset () { cancelled.store (false); }

private:
	std::atomic<bool> cancelled {false};
};

/** Fortschritt einer Übertragung in Bytes; `total` ist 0, wenn unbekannt. */
using HttpProgress = std::function<void (std::uint64_t sent, std::uint64_t total)>;

class HttpClient {
public:
	virtual ~HttpClient () = default;
	virtual Result<HttpResponse> Send (const HttpRequest& request, CancelToken* cancel,
									   const HttpProgress& progress) = 0;
	Result<HttpResponse> Send (const HttpRequest& request) { return Send (request, nullptr, {}); }
};

/** libcurl-Umsetzung; der einzige Ort im Kern, der ein Netzwerk kennt. */
std::unique_ptr<HttpClient> MakeCurlHttpClient ();

} // namespace rtx
