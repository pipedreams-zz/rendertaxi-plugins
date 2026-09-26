// Credential-Manager-Umsetzung des Tokenspeichers (Windows).
//
// Das Gegenstück zur Keychain: ein generischer Eintrag je Serveradresse, Ziel
// `rendertaxi/archicad/<Serveradresse>`. Wie dort ist die Serveradresse der
// Schlüssel, damit Anmeldungen an zwei Servern sich nicht überschreiben, und
// wie dort liegt im Eintrag derselbe Datensatz (`EncodeCredential`).
//
// `CRED_PERSIST_LOCAL_MACHINE`: der Eintrag gehört dem angemeldeten
// Windows-Benutzer, überlebt eine Abmeldung von Windows und wandert nicht mit
// einem servergespeicherten Profil auf andere Rechner — dasselbe, was die
// Keychain mit `kSecAttrAccessibleWhenUnlocked` ohne iCloud-Abgleich tut.
// Windows verschlüsselt den Inhalt mit dem Schlüssel des Benutzers (DPAPI).
#include "rtx/TokenStore.hpp"

#if defined (_WIN32)

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincred.h>

#include "rtx/Platform.hpp"

namespace rtx {

std::wstring CredentialTarget (const std::string& serverUrl)
{
	return L"rendertaxi/archicad/" + Widen (serverUrl);
}

namespace {

class CredentialManagerTokenStore final : public TokenStore {
public:
	Result<StoredCredential> LoadRaw (const std::string& serverUrl) override
	{
		const Result<std::string> raw = Read (serverUrl);
		if (!raw) return Result<StoredCredential>::Fail (raw.GetError ());
		return DecodeRawCredential (raw.Value ());
	}

	Result<StoredCredential> Load (const std::string& serverUrl) override
	{
		const Result<std::string> raw = Read (serverUrl);
		if (!raw) return Result<StoredCredential>::Fail (raw.GetError ());
		return DecodeCredential (raw.Value ());
	}

	Status Save (const std::string& serverUrl, const StoredCredential& credential) override
	{
		std::string encoded = EncodeCredential (credential);
		// Die Obergrenze des Systems für den Inhalt eines Eintrags. Ein
		// gekürzter Datensatz wäre ein kaputtes Token; also ausdrücklich ab.
		if (encoded.size () > CRED_MAX_CREDENTIAL_BLOB_SIZE)
			return Status::Fail (errc::IoFailed,
								 "Der Anmeldedatensatz ist zu groß für den Windows-Anmeldeinformationsspeicher.");

		std::wstring target = CredentialTarget (serverUrl);
		std::wstring user = L"rendertaxi.ai";
		CREDENTIALW entry = {};
		entry.Type = CRED_TYPE_GENERIC;
		entry.TargetName = &target[0];
		entry.UserName = &user[0];
		entry.CredentialBlobSize = static_cast<DWORD> (encoded.size ());
		entry.CredentialBlob = reinterpret_cast<LPBYTE> (&encoded[0]);
		entry.Persist = CRED_PERSIST_LOCAL_MACHINE;

		const BOOL written = CredWriteW (&entry, 0);
		SecureZeroMemory (&encoded[0], encoded.size ());
		if (!written)
			return Status::Fail (errc::IoFailed,
								 "Der Windows-Anmeldeinformationsspeicher hat das Anmeldetoken nicht "
								 "angenommen (Fehler " + std::to_string (GetLastError ()) + ").");
		return Status::Ok ();
	}

	Status Erase (const std::string& serverUrl) override
	{
		// Das **Token** verschwindet, die Gerätekennung bleibt (§5.5).
		StoredCredential kept;
		const Result<StoredCredential> raw = LoadRaw (serverUrl);
		if (raw) kept.deviceId = raw.Value ().deviceId;

		const std::wstring target = CredentialTarget (serverUrl);
		if (!CredDeleteW (target.c_str (), CRED_TYPE_GENERIC, 0) && GetLastError () != ERROR_NOT_FOUND)
			return Status::Fail (errc::IoFailed, "Das Anmeldetoken ließ sich nicht löschen.");
		if (kept.deviceId.empty ()) return Status::Ok ();
		return Save (serverUrl, kept);
	}

private:
	Result<std::string> Read (const std::string& serverUrl)
	{
		const std::wstring target = CredentialTarget (serverUrl);
		PCREDENTIALW entry = nullptr;
		if (!CredReadW (target.c_str (), CRED_TYPE_GENERIC, 0, &entry) || entry == nullptr)
			return Result<std::string>::Fail (
				errc::Unauthorized, "Kein Anmeldetoken im Windows-Anmeldeinformationsspeicher.");
		const std::string encoded (reinterpret_cast<const char*> (entry->CredentialBlob),
								   static_cast<std::size_t> (entry->CredentialBlobSize));
		SecureZeroMemory (entry->CredentialBlob, entry->CredentialBlobSize);
		CredFree (entry);
		return Result<std::string>::Ok (encoded);
	}
};

} // namespace

std::unique_ptr<TokenStore> MakeCredentialManagerTokenStore ()
{
	return std::unique_ptr<TokenStore> (new CredentialManagerTokenStore ());
}

} // namespace rtx

#endif
