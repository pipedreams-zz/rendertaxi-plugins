#include "rtx/Http.hpp"

#include <cctype>
#include <cstdio>

#include <curl/curl.h>

#include "rtx/Platform.hpp"

namespace rtx {
namespace {

std::string ToLower (const std::string& value)
{
	std::string out = value;
	for (char& c : out) c = static_cast<char> (std::tolower (static_cast<unsigned char> (c)));
	return out;
}

struct Sink {
	std::string* body;
	HttpHeaders* headers;
};

std::size_t WriteBody (char* data, std::size_t size, std::size_t count, void* userdata)
{
	const std::size_t total = size * count;
	static_cast<Sink*> (userdata)->body->append (data, total);
	return total;
}

std::size_t WriteHeader (char* data, std::size_t size, std::size_t count, void* userdata)
{
	const std::size_t total = size * count;
	std::string line (data, total);
	while (!line.empty () && (line.back () == '\r' || line.back () == '\n')) line.pop_back ();
	const std::size_t colon = line.find (':');
	if (colon != std::string::npos) {
		std::string name = line.substr (0, colon);
		std::string value = line.substr (colon + 1);
		while (!value.empty () && value.front () == ' ') value.erase (value.begin ());
		static_cast<Sink*> (userdata)->headers->emplace_back (std::move (name), std::move (value));
	}
	return total;
}

struct ProgressState {
	CancelToken* cancel;
	const HttpProgress* progress;
};

int OnProgress (void* userdata, curl_off_t downTotal, curl_off_t downNow, curl_off_t upTotal,
				curl_off_t upNow)
{
	ProgressState* state = static_cast<ProgressState*> (userdata);
	if (state->cancel != nullptr && state->cancel->IsCancelled ()) return 1;   // bricht ab
	if (state->progress != nullptr && *state->progress) {
		const bool uploading = upTotal > 0 || upNow > 0;
		const curl_off_t now = uploading ? upNow : downNow;
		const curl_off_t total = uploading ? upTotal : downTotal;
		(*state->progress) (static_cast<std::uint64_t> (now < 0 ? 0 : now),
							static_cast<std::uint64_t> (total < 0 ? 0 : total));
	}
	return 0;
}

class CurlHttpClient final : public HttpClient {
public:
	CurlHttpClient () { curl_global_init (CURL_GLOBAL_DEFAULT); }
	~CurlHttpClient () override { curl_global_cleanup (); }

	Result<HttpResponse> Send (const HttpRequest& request, CancelToken* cancel,
							   const HttpProgress& progress) override
	{
		if (cancel != nullptr && cancel->IsCancelled ())
			return Result<HttpResponse>::Fail (errc::Cancelled, "Vorgang abgebrochen.");

		CURL* handle = curl_easy_init ();
		if (handle == nullptr)
			return Result<HttpResponse>::Fail (errc::Transport, "HTTP-Client nicht verfügbar.");

		HttpResponse response;
		Sink sink {&response.body, &response.headers};
		ProgressState progressState {cancel, &progress};

		curl_easy_setopt (handle, CURLOPT_URL, request.url.c_str ());
		curl_easy_setopt (handle, CURLOPT_WRITEFUNCTION, WriteBody);
		curl_easy_setopt (handle, CURLOPT_WRITEDATA, &sink);
		curl_easy_setopt (handle, CURLOPT_HEADERFUNCTION, WriteHeader);
		curl_easy_setopt (handle, CURLOPT_HEADERDATA, &sink);
		curl_easy_setopt (handle, CURLOPT_NOPROGRESS, 0L);
		curl_easy_setopt (handle, CURLOPT_XFERINFOFUNCTION, OnProgress);
		curl_easy_setopt (handle, CURLOPT_XFERINFODATA, &progressState);
		curl_easy_setopt (handle, CURLOPT_TIMEOUT, static_cast<long> (request.timeoutSeconds));
		curl_easy_setopt (handle, CURLOPT_CONNECTTIMEOUT, 15L);
		curl_easy_setopt (handle, CURLOPT_FOLLOWLOCATION, 0L);
		curl_easy_setopt (handle, CURLOPT_ACCEPT_ENCODING, "");
		curl_easy_setopt (handle, CURLOPT_USERAGENT, "rendertaxi-archicad-addon");

		struct curl_slist* headerList = nullptr;
		for (const auto& header : request.headers) {
			const std::string line = header.first + ": " + header.second;
			headerList = curl_slist_append (headerList, line.c_str ());
		}
		// Ohne Expect-Handshake: er kostet bei jedem PUT eine Rundreise.
		headerList = curl_slist_append (headerList, "Expect:");
		curl_easy_setopt (handle, CURLOPT_HTTPHEADER, headerList);

		std::FILE* upload = nullptr;
		if (!request.bodyFilePath.empty ()) {
			upload = OpenFile (request.bodyFilePath, "rb");
			if (upload == nullptr) {
				curl_slist_free_all (headerList);
				curl_easy_cleanup (handle);
				return Result<HttpResponse>::Fail (errc::IoFailed,
												   "Datei nicht lesbar: " + request.bodyFilePath);
			}
			std::fseek (upload, 0, SEEK_END);
			const long size = std::ftell (upload);
			std::fseek (upload, 0, SEEK_SET);
			curl_easy_setopt (handle, CURLOPT_UPLOAD, 1L);
			curl_easy_setopt (handle, CURLOPT_READDATA, upload);
			curl_easy_setopt (handle, CURLOPT_INFILESIZE_LARGE, static_cast<curl_off_t> (size));
			curl_easy_setopt (handle, CURLOPT_CUSTOMREQUEST, request.method.c_str ());
		} else if (request.method == "POST" || request.method == "PUT" ||
				   request.method == "PATCH" || request.method == "DELETE") {
			curl_easy_setopt (handle, CURLOPT_CUSTOMREQUEST, request.method.c_str ());
			curl_easy_setopt (handle, CURLOPT_POSTFIELDS, request.body.c_str ());
			curl_easy_setopt (handle, CURLOPT_POSTFIELDSIZE,
							  static_cast<long> (request.body.size ()));
		}

		const CURLcode code = curl_easy_perform (handle);
		long status = 0;
		curl_easy_getinfo (handle, CURLINFO_RESPONSE_CODE, &status);
		response.status = static_cast<int> (status);

		if (upload != nullptr) std::fclose (upload);
		curl_slist_free_all (headerList);
		curl_easy_cleanup (handle);

		if (code == CURLE_ABORTED_BY_CALLBACK)
			return Result<HttpResponse>::Fail (errc::Cancelled, "Vorgang abgebrochen.");
		if (code != CURLE_OK) {
			// Bewusst ohne URL: sie kann eine signierte Adresse sein
			// (`upload-protocol.md`, Abschnitt 9).
			return Result<HttpResponse>::Fail (
				errc::Transport, std::string ("Verbindung fehlgeschlagen: ") + curl_easy_strerror (code));
		}
		return Result<HttpResponse>::Ok (response);
	}
};

} // namespace

std::string HttpResponse::Header (const std::string& name) const
{
	const std::string wanted = ToLower (name);
	for (const auto& header : headers) {
		if (ToLower (header.first) == wanted) return header.second;
	}
	return {};
}

std::unique_ptr<HttpClient> MakeCurlHttpClient ()
{
	return std::unique_ptr<HttpClient> (new CurlHttpClient ());
}

} // namespace rtx
