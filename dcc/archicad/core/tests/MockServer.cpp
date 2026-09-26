#include "MockServer.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cctype>
#include <cstring>
#include <sstream>

namespace testing {
namespace {

std::string ToLower (std::string value)
{
	for (char& c : value) c = static_cast<char> (std::tolower (static_cast<unsigned char> (c)));
	return value;
}

bool ReadLine (int fd, std::string& buffer, std::string& line)
{
	while (true) {
		const std::size_t newline = buffer.find ("\r\n");
		if (newline != std::string::npos) {
			line = buffer.substr (0, newline);
			buffer.erase (0, newline + 2);
			return true;
		}
		char chunk[4096];
		const ssize_t read = ::recv (fd, chunk, sizeof chunk, 0);
		if (read <= 0) return false;
		buffer.append (chunk, static_cast<std::size_t> (read));
	}
}

bool ReadExactly (int fd, std::string& buffer, std::size_t length, std::string& out)
{
	while (buffer.size () < length) {
		char chunk[8192];
		const ssize_t read = ::recv (fd, chunk, sizeof chunk, 0);
		if (read <= 0) return false;
		buffer.append (chunk, static_cast<std::size_t> (read));
	}
	out = buffer.substr (0, length);
	buffer.erase (0, length);
	return true;
}

} // namespace

std::string MockRequest::Header (const std::string& name) const
{
	const auto found = headers.find (ToLower (name));
	return found == headers.end () ? std::string () : found->second;
}

MockServer::MockServer (MockHandler requestHandler, int port) :
	handler (std::move (requestHandler))
{
	listenFd = ::socket (AF_INET, SOCK_STREAM, 0);
	int reuse = 1;
	::setsockopt (listenFd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof reuse);

	sockaddr_in address {};
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = htonl (INADDR_LOOPBACK);
	address.sin_port = htons (static_cast<uint16_t> (port));
	if (::bind (listenFd, reinterpret_cast<sockaddr*> (&address), sizeof address) != 0 &&
		port != 0) {
		// Belegter Wunschport: lieber ein freier als gar keiner.
		address.sin_port = 0;
		::bind (listenFd, reinterpret_cast<sockaddr*> (&address), sizeof address);
	}
	::listen (listenFd, 16);

	socklen_t length = sizeof address;
	::getsockname (listenFd, reinterpret_cast<sockaddr*> (&address), &length);
	std::ostringstream url;
	url << "http://127.0.0.1:" << ntohs (address.sin_port);
	baseUrl = url.str ();

	running.store (true);
	worker = std::thread (&MockServer::Serve, this);
}

MockServer::~MockServer ()
{
	running.store (false);
	if (listenFd >= 0) ::shutdown (listenFd, SHUT_RDWR);
	if (listenFd >= 0) ::close (listenFd);
	if (worker.joinable ()) worker.join ();
}

void MockServer::Serve ()
{
	while (running.load ()) {
		const int client = ::accept (listenFd, nullptr, nullptr);
		if (client < 0) break;

		std::string buffer;
		std::string line;
		if (!ReadLine (client, buffer, line)) { ::close (client); continue; }

		MockRequest request;
		std::istringstream requestLine (line);
		std::string target;
		requestLine >> request.method >> target;
		const std::size_t question = target.find ('?');
		if (question == std::string::npos) {
			request.path = target;
		} else {
			request.path = target.substr (0, question);
			request.query = target.substr (question + 1);
		}

		std::size_t contentLength = 0;
		while (ReadLine (client, buffer, line) && !line.empty ()) {
			const std::size_t colon = line.find (':');
			if (colon == std::string::npos) continue;
			std::string name = ToLower (line.substr (0, colon));
			std::string value = line.substr (colon + 1);
			while (!value.empty () && value.front () == ' ') value.erase (value.begin ());
			if (name == "content-length") contentLength = static_cast<std::size_t> (std::stoul (value));
			request.headers[name] = value;
		}
		if (contentLength > 0) ReadExactly (client, buffer, contentLength, request.body);

		++requestCount;
		const MockResponse response = handler (request);

		std::ostringstream out;
		out << "HTTP/1.1 " << response.status << " OK\r\n";
		out << "Content-Type: " << response.contentType << "\r\n";
		out << "Content-Length: " << response.body.size () << "\r\n";
		for (const auto& header : response.headers)
			out << header.first << ": " << header.second << "\r\n";
		out << "Connection: close\r\n\r\n";
		out << response.body;
		const std::string payload = out.str ();
		::send (client, payload.data (), payload.size (), 0);
		::close (client);
	}
}

} // namespace testing
