// Keychain-Umsetzung des Tokenspeichers.
//
// Dienst ist konstant, Konto ist die Serveradresse: ein Rechner kann sich an
// `https://dev.rendertaxi.ai` und an einer lokalen Instanz gleichzeitig
// angemeldet haben, ohne dass sich die Einträge überschreiben.
#include "rtx/TokenStore.hpp"

#if defined (__APPLE__)

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include <vector>

namespace rtx {
namespace {

constexpr const char* kService = "ai.rendertaxi.archicad";

CFStringRef MakeCFString (const std::string& value)
{
	return CFStringCreateWithBytes (kCFAllocatorDefault,
									reinterpret_cast<const UInt8*> (value.data ()),
									static_cast<CFIndex> (value.size ()), kCFStringEncodingUTF8,
									false);
}

CFDataRef MakeCFData (const std::string& value)
{
	return CFDataCreate (kCFAllocatorDefault, reinterpret_cast<const UInt8*> (value.data ()),
						 static_cast<CFIndex> (value.size ()));
}

CFMutableDictionaryRef BaseQuery (const std::string& account)
{
	CFMutableDictionaryRef query =
		CFDictionaryCreateMutable (kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks,
								   &kCFTypeDictionaryValueCallBacks);
	CFDictionarySetValue (query, kSecClass, kSecClassGenericPassword);
	CFStringRef service = MakeCFString (kService);
	CFStringRef accountRef = MakeCFString (account);
	CFDictionarySetValue (query, kSecAttrService, service);
	CFDictionarySetValue (query, kSecAttrAccount, accountRef);
	CFRelease (service);
	CFRelease (accountRef);
	return query;
}

class KeychainTokenStore final : public TokenStore {
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

	Result<std::string> Read (const std::string& serverUrl)
	{
		CFMutableDictionaryRef query = BaseQuery (serverUrl);
		CFDictionarySetValue (query, kSecReturnData, kCFBooleanTrue);
		CFDictionarySetValue (query, kSecMatchLimit, kSecMatchLimitOne);

		CFTypeRef item = nullptr;
		const OSStatus status = SecItemCopyMatching (query, &item);
		CFRelease (query);
		if (status != errSecSuccess || item == nullptr)
			return Result<std::string>::Fail (errc::Unauthorized,
											  "Kein Anmeldetoken in der Keychain.");

		CFDataRef data = static_cast<CFDataRef> (item);
		const std::string encoded (reinterpret_cast<const char*> (CFDataGetBytePtr (data)),
								   static_cast<std::size_t> (CFDataGetLength (data)));
		CFRelease (item);
		return Result<std::string>::Ok (encoded);
	}

	Status Save (const std::string& serverUrl, const StoredCredential& credential) override
	{
		const std::string encoded = EncodeCredential (credential);
		CFDataRef payload = MakeCFData (encoded);

		CFMutableDictionaryRef query = BaseQuery (serverUrl);
		CFMutableDictionaryRef update =
			CFDictionaryCreateMutable (kCFAllocatorDefault, 0, &kCFTypeDictionaryKeyCallBacks,
									   &kCFTypeDictionaryValueCallBacks);
		CFDictionarySetValue (update, kSecValueData, payload);
		OSStatus status = SecItemUpdate (query, update);
		if (status == errSecItemNotFound) {
			CFDictionarySetValue (query, kSecValueData, payload);
			CFDictionarySetValue (query, kSecAttrAccessible, kSecAttrAccessibleWhenUnlocked);
			status = SecItemAdd (query, nullptr);
		}
		CFRelease (update);
		CFRelease (query);
		CFRelease (payload);

		if (status != errSecSuccess)
			return Status::Fail (errc::IoFailed,
								 "Die Keychain hat das Anmeldetoken nicht angenommen.");
		return Status::Ok ();
	}

	Status Erase (const std::string& serverUrl) override
	{
		// Das **Token** verschwindet, die Gerätekennung bleibt (§5.5).
		StoredCredential kept;
		const Result<StoredCredential> raw = LoadRaw (serverUrl);
		if (raw) kept.deviceId = raw.Value ().deviceId;

		CFMutableDictionaryRef query = BaseQuery (serverUrl);
		const OSStatus status = SecItemDelete (query);
		CFRelease (query);
		if (status != errSecSuccess && status != errSecItemNotFound)
			return Status::Fail (errc::IoFailed, "Das Anmeldetoken ließ sich nicht löschen.");
		if (kept.deviceId.empty ()) return Status::Ok ();
		return Save (serverUrl, kept);
	}
};

} // namespace

std::unique_ptr<TokenStore> MakeKeychainTokenStore ()
{
	return std::unique_ptr<TokenStore> (new KeychainTokenStore ());
}

} // namespace rtx

#else

namespace rtx {

std::unique_ptr<TokenStore> MakeKeychainTokenStore ()
{
	// Windows bekäme hier den Credential Manager. Issue #20 nennt den
	// Windows-Build ausdrücklich als Nicht-Ziel; eine Dateifassung wäre die
	// Klartextablage, die Festlegung 4 verbietet.
	return nullptr;
}

} // namespace rtx

#endif
