// Winziger HTTP/1.1-Server für die Tests.
//
// Stufe D des Auftrags verlangt „automatisierte Tests für Uploader und
// Zustandsautomat ohne Archicad gegen einen lokalen Scheinserver". Der Server
// spricht echtes HTTP über eine echte Verbindung, damit auch der libcurl-Client
// mitgeprüft wird und nicht nur eine Attrappe.
#pragma once

#include <atomic>
#include <functional>
#include <map>
#include <string>
#include <thread>
#include <vector>

namespace testing {

struct MockRequest {
	std::string method;
	std::string path;
	std::string query;
	std::map<std::string, std::string> headers;
	std::string body;

	std::string Header (const std::string& name) const;
};

struct MockResponse {
	int status = 200;
	std::string contentType = "application/json";
	std::string body;
	std::vector<std::pair<std::string, std::string>> headers;
};

using MockHandler = std::function<MockResponse (const MockRequest&)>;

class MockServer final {
public:
	/** `port` 0 wählt einen freien Port; ein belegter Port fällt darauf zurück. */
	explicit MockServer (MockHandler handler, int port = 0);
	~MockServer ();

	/** Basisadresse, etwa `http://127.0.0.1:52341`. */
	const std::string& BaseUrl () const { return baseUrl; }
	int RequestCount () const { return requestCount.load (); }

private:
	void Serve ();

	MockHandler handler;
	int listenFd = -1;
	std::string baseUrl;
	std::thread worker;
	std::atomic<bool> running {false};
	std::atomic<int> requestCount {0};
};

} // namespace testing
