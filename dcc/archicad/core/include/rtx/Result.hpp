// Ergebnis- und Fehlertyp des DevKit-freien Kerns.
//
// Der Kern wirft keine Ausnahmen über seine Grenze: ein Add-On, das in einem
// fremden Prozess läuft, meldet Fehler als Wert. `Error::code` ist der
// maschinenlesbare Fehlercode des Vertrags (upload-protocol.md, Abschnitt 7),
// `message` der Text, den die Palette zeigt.
#pragma once

#include <string>
#include <utility>

namespace rtx {

struct Error {
	std::string code;
	std::string message;
	/** JSON Pointer auf die Fundstelle, falls der Fehler aus einer Prüfung stammt. */
	std::string pointer;

	Error () = default;
	Error (std::string c, std::string m, std::string p = {}) :
		code (std::move (c)), message (std::move (m)), pointer (std::move (p))
	{
	}
};

/** Fehlercodes, die ausschließlich im Client entstehen. */
namespace errc {
inline constexpr const char* SchemaInvalid = "schema_invalid";
inline constexpr const char* ContentHashMismatch = "content_hash_mismatch";
inline constexpr const char* LimitExceeded = "limit_exceeded";
inline constexpr const char* IdempotencyConflict = "idempotency_conflict";
inline constexpr const char* UnsupportedContractMajor = "unsupported_contract_major";
inline constexpr const char* UnsupportedContractMinor = "unsupported_contract_minor";
inline constexpr const char* Transport = "transport_failed";
inline constexpr const char* Cancelled = "cancelled";
inline constexpr const char* Unauthorized = "unauthorized";
inline constexpr const char* NotCanonical = "not_canonical";
inline constexpr const char* IoFailed = "io_failed";
inline constexpr const char* AuthorizationPending = "authorization_pending";
inline constexpr const char* SlowDown = "slow_down";
inline constexpr const char* ExpiredToken = "expired_token";
inline constexpr const char* AccessDenied = "access_denied";
/** Der Server kennt diesen Endpunkt nicht — die Gegenstelle ist noch nicht ausgeliefert. */
inline constexpr const char* EndpointMissing = "endpoint_missing";
inline constexpr const char* Forbidden = "forbidden";
/** `429` an den vier unangemeldeten Operationen; `Error::pointer` trägt `Retry-After`. */
inline constexpr const char* RateLimited = "rate_limited";
/** Ein Anmeldetoken sollte über eine unverschlüsselte Verbindung gehen (F-01). */
inline constexpr const char* InsecureTransport = "insecure_transport";
/** Zustandscodes des Capture-Ablaufs (`plugin-api-v1.md`, Abschnitt 7.8). */
inline constexpr const char* CaptureStateConflict = "capture_state_conflict";
inline constexpr const char* CaptureClosed = "capture_closed";
inline constexpr const char* CaptureExpired = "capture_expired";
/** Formverletzung des Idempotenzschlüssels (§7.3). */
inline constexpr const char* InvalidIdempotencyKey = "invalid_idempotency_key";
} // namespace errc

/**
 * Entweder ein Wert oder ein Fehler. Bewusst klein gehalten: `std::expected`
 * setzt C++23 voraus, das DevKit schreibt C++17 vor.
 */
template <typename T>
class Result final {
public:
	static Result Ok (T value) { return Result (std::move (value), {}, true); }
	static Result Fail (Error error) { return Result (T {}, std::move (error), false); }
	static Result Fail (std::string code, std::string message)
	{
		return Fail (Error (std::move (code), std::move (message)));
	}

	bool IsOk () const { return ok; }
	explicit operator bool () const { return ok; }

	const T& Value () const { return value; }
	T& Value () { return value; }
	const Error& GetError () const { return error; }

private:
	Result (T v, Error e, bool o) : value (std::move (v)), error (std::move (e)), ok (o) {}

	T value;
	Error error;
	bool ok = false;
};

/** Ergebnis ohne Nutzwert. */
class Status final {
public:
	static Status Ok () { return Status ({}, true); }
	static Status Fail (Error error) { return Status (std::move (error), false); }
	static Status Fail (std::string code, std::string message)
	{
		return Fail (Error (std::move (code), std::move (message)));
	}

	bool IsOk () const { return ok; }
	explicit operator bool () const { return ok; }
	const Error& GetError () const { return error; }

private:
	Status (Error e, bool o) : error (std::move (e)), ok (o) {}

	Error error;
	bool ok = false;
};

} // namespace rtx
