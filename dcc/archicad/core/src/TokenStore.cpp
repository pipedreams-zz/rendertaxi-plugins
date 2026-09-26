#include "rtx/TokenStore.hpp"

#include <map>
#include <mutex>

#include "rtx/Ids.hpp"
#include "rtx/Json.hpp"

namespace rtx {

std::string EncodeCredential (const StoredCredential& credential)
{
	JsonPtr node = Json::MakeObject ();
	node->Set ("accessToken", Json::MakeString (credential.accessToken));
	node->Set ("deviceId", Json::MakeString (credential.deviceId));
	node->Set ("expiresAtMillis",
			   Json::MakeInt (static_cast<std::int64_t> (credential.expiresAtMillis)));
	node->Set ("displayName", Json::MakeString (credential.displayName));
	node->Set ("organizationId", Json::MakeString (credential.organizationId));
	node->Set ("organizationName", Json::MakeString (credential.organizationName));
	node->Set ("membershipIssue", Json::MakeString (credential.membershipIssue));
	return node->Serialize ();
}

Result<StoredCredential> DecodeRawCredential (const std::string& encoded)
{
	const Result<StoredCredential> decoded = DecodeCredential (encoded);
	if (decoded) return decoded;
	// Kein Token, aber vielleicht eine Gerätekennung.
	const JsonPtr node = Json::Parse (encoded);
	if (node == nullptr || node->GetKind () != Json::Kind::Object) return decoded;
	StoredCredential credential;
	if (const JsonPtr field = node->Get ("deviceId")) credential.deviceId = field->StringOr ("");
	if (credential.deviceId.empty ()) return decoded;
	return Result<StoredCredential>::Ok (credential);
}

Result<StoredCredential> DecodeCredential (const std::string& encoded)
{
	const JsonPtr node = Json::Parse (encoded);
	if (node == nullptr || node->GetKind () != Json::Kind::Object)
		return Result<StoredCredential>::Fail (errc::Unauthorized,
											   "Gespeicherter Anmeldedatensatz ist unlesbar.");
	StoredCredential credential;
	if (const JsonPtr field = node->Get ("accessToken")) credential.accessToken = field->StringOr ("");
	if (const JsonPtr field = node->Get ("deviceId")) credential.deviceId = field->StringOr ("");
	if (const JsonPtr field = node->Get ("expiresAtMillis"))
		credential.expiresAtMillis = static_cast<std::uint64_t> (field->IntOr (0));
	if (const JsonPtr field = node->Get ("displayName")) credential.displayName = field->StringOr ("");
	if (const JsonPtr field = node->Get ("organizationId"))
		credential.organizationId = field->StringOr ("");
	if (const JsonPtr field = node->Get ("organizationName"))
		credential.organizationName = field->StringOr ("");
	if (const JsonPtr field = node->Get ("membershipIssue"))
		credential.membershipIssue = field->StringOr ("");
	if (credential.accessToken.empty ())
		return Result<StoredCredential>::Fail (errc::Unauthorized, "Kein Anmeldetoken hinterlegt.");
	return Result<StoredCredential>::Ok (credential);
}

std::string LoadOrCreateDeviceId (TokenStore& tokens, const std::string& serverUrl)
{
	// `Load` scheitert, sobald kein Token da ist — die Gerätekennung steht
	// trotzdem im Datensatz. Deshalb wird hier der rohe Datensatz gelesen und
	// nicht `Load` benutzt.
	const Result<StoredCredential> stored = tokens.LoadRaw (serverUrl);
	if (stored && !stored.Value ().deviceId.empty ()) return stored.Value ().deviceId;

	StoredCredential credential = stored ? stored.Value () : StoredCredential {};
	credential.deviceId = NewUuidV7 ();
	tokens.Save (serverUrl, credential);
	return credential.deviceId;
}

namespace {

class MemoryTokenStore final : public TokenStore {
public:
	Result<StoredCredential> Load (const std::string& serverUrl) override
	{
		std::lock_guard<std::mutex> guard (mutex);
		const auto found = entries.find (serverUrl);
		if (found == entries.end ())
			return Result<StoredCredential>::Fail (errc::Unauthorized,
												   "Kein Anmeldetoken hinterlegt.");
		return DecodeCredential (found->second);
	}

	Result<StoredCredential> LoadRaw (const std::string& serverUrl) override
	{
		std::lock_guard<std::mutex> guard (mutex);
		const auto found = entries.find (serverUrl);
		if (found == entries.end ())
			return Result<StoredCredential>::Fail (errc::Unauthorized, "Kein Datensatz.");
		return DecodeRawCredential (found->second);
	}

	Status Save (const std::string& serverUrl, const StoredCredential& credential) override
	{
		std::lock_guard<std::mutex> guard (mutex);
		entries[serverUrl] = EncodeCredential (credential);
		return Status::Ok ();
	}

	Status Erase (const std::string& serverUrl) override
	{
		// Das **Token** verschwindet, die Gerätekennung bleibt.
		StoredCredential kept;
		{
			std::lock_guard<std::mutex> guard (mutex);
			const auto found = entries.find (serverUrl);
			if (found == entries.end ()) return Status::Ok ();
			const Result<StoredCredential> raw = DecodeRawCredential (found->second);
			if (raw) kept.deviceId = raw.Value ().deviceId;
			entries.erase (found);
		}
		if (kept.deviceId.empty ()) return Status::Ok ();
		return Save (serverUrl, kept);
	}

private:
	std::mutex mutex;
	std::map<std::string, std::string> entries;
};

} // namespace

std::unique_ptr<TokenStore> MakeMemoryTokenStore ()
{
	return std::unique_ptr<TokenStore> (new MemoryTokenStore ());
}

} // namespace rtx
