// Prüfungen der vertragsnahen Bausteine: Hash, Kanonisierung, Manifest,
// Kennungen, Bildkopf, Protokoll.
#include "Testing.hpp"

#include <cstdlib>

#include "rtx/Canonical.hpp"
#include "rtx/CaptureManifest.hpp"
#include "rtx/Ids.hpp"
#include "rtx/ImageCrop.hpp"
#include "rtx/ImageFile.hpp"
#include "rtx/Json.hpp"
#include "rtx/Log.hpp"
#include "rtx/Sha256.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/TransferStore.hpp"

// Nur für die Kennung und die Fassung des Add-Ons; kein DevKit.
#include "Version.hpp"

using namespace rtx;

namespace {

/** Ein gültiges Manifest mit einer `viewport`-Rolle. */
CaptureManifest MakeManifest ()
{
	CaptureManifest manifest;
	manifest.captureId = "0199a000-0000-7000-8000-000000000001";
	manifest.createdAt = "2026-09-20T10:00:00.000Z";
	manifest.source.hostKey = "archicad";
	manifest.source.hostVersion = "28.0.0";
	manifest.source.hostBuild = "7006";
	// §5.1: wortgleich zu `client_id`.
	manifest.source.pluginIdentifier = kPluginClientId;
	manifest.source.pluginVersion = "1.0.0";
	manifest.source.os = "macos";
	manifest.source.osVersion = "15.6";
	manifest.source.architecture = "arm64";
	manifest.sourceProjectKey = "archicad:project:abc";
	manifest.sourceViewKey = "archicad:view:3d";
	manifest.viewDisplayName = "Generelle Perspektive";

	CaptureAsset viewport;
	viewport.role = "viewport";
	viewport.path = "viewport.png";
	viewport.status = "present";
	viewport.mediaType = "image/png";
	viewport.byteSize = 1234;
	viewport.sha256 = std::string (64, 'a');
	viewport.hasImage = true;
	viewport.image.width = 1920;
	viewport.image.height = 1080;
	viewport.image.colorSpace = "srgb";
	viewport.image.bitDepth = 8;
	viewport.image.sampleFormat = "uint";
	viewport.image.channels = "rgba";
	manifest.assets.push_back (viewport);
	return manifest;
}

} // namespace

RTX_TEST (KennungDesPluginsIstWortgleichZurClientId)
{
	// §5.1: „`client_id` ist die Kennung des Plugins in Reverse-DNS-Form,
	// wortgleich zu `source.plugin.identifier` des Manifests." Vorher standen
	// im Add-On zwei verschiedene Kennungen (F-05).
	RTX_CHECK_EQ (std::string (RTX_ADDON_IDENTIFIER), std::string (kPluginClientId));
}

RTX_TEST (Sha256EntsprichtDenBekanntenVektoren)
{
	RTX_CHECK_EQ (Sha256::OfString (""),
				  std::string ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
	RTX_CHECK_EQ (Sha256::OfString ("abc"),
				  std::string ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
	// Genau 64 Byte: die Blockgrenze, an der eine fehlerhafte Auffüllung auffällt.
	RTX_CHECK_EQ (Sha256::OfString (std::string (64, 'a')),
				  std::string ("ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"));
	// 1.000.000 mal 'a' — der klassische Langlauf über viele Blöcke.
	Sha256 million;
	for (int i = 0; i < 1000; ++i) million.Update (std::string (1000, 'a'));
	RTX_CHECK_EQ (million.Hex (),
				  std::string ("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));
}

RTX_TEST (KodierungIstLaengenpraefigiertUndLehntNichtAsciiAb)
{
	RTX_CHECK_EQ (EncodeField ("Wall").Value (), std::string ("4:Wall"));
	RTX_CHECK_EQ (EncodeField ("").Value (), std::string ("0:"));
	// Ein fehlender Wert ist von der Zeichenkette "-" unterscheidbar.
	RTX_CHECK (EncodeAbsent () != EncodeField ("-").Value ());
	RTX_CHECK_EQ (EncodeField ("-").Value (), std::string ("1:-"));
	RTX_CHECK (!EncodeField ("Sch\xc3\xb6n"));
	RTX_CHECK_EQ (EncodeField ("Sch\xc3\xb6n").GetError ().code, std::string (errc::NotCanonical));
}

RTX_TEST (SortierungFolgtDenUtf8Bytes)
{
	// U+FF3A (ef bc ba) steht in UTF-8 hinter U+1D400 (f0 9d 90 80)? Nein:
	// 0xEF < 0xF0, also davor. In der UTF-16-Ordnung wäre es umgekehrt, weil
	// U+1D400 als Surrogatpaar 0xD835 beginnt.
	std::vector<std::string> values {"\xf0\x9d\x90\x80", "\xef\xbc\xba"};
	SortByUtf8 (values);
	RTX_CHECK_EQ (values.front (), std::string ("\xef\xbc\xba"));
}

RTX_TEST (ContentHashFolgtDerAssetzeileDesVertrags)
{
	// Referenzwert aus der Regel in architecture.md, Abschnitt 8.6:
	// SHA-256 über `enc("asset") ⇥ enc(role) ⇥ enc(path) ⇥ enc(sha256)` + "\n".
	const std::string sha (64, 'a');
	const std::string line = "5:asset\t8:viewport\t12:viewport.png\t64:" + sha + "\n";
	const std::vector<ContentHashAsset> assets {{"viewport", "viewport.png", sha}};
	RTX_CHECK_EQ (CaptureContentHash (assets).Value (), Sha256::OfString (line));
}

RTX_TEST (ContentHashIstBitgleichZurReferenzumsetzung)
{
	// Golden-Vektoren, erzeugt mit `integrations/_shared/tools/canonical-hash.mjs`
	// aus PR #129 (Issue #16). Sie halten die sprachübergreifende Bitgleichheit
	// fest, ohne dass der Testlauf Node oder das noch offene Issue braucht.
	// Erzeugt mit:
	//   node -e 'import("…/canonical-hash.mjs").then(m => console.log(m.contentHash(doc)))'
	const std::string a (64, 'a');
	const std::string b (64, 'b');

	const std::vector<ContentHashAsset> one {{"viewport", "viewport.png", a}};
	RTX_CHECK_EQ (CaptureContentHash (one).Value (),
				  std::string ("b3c21248aa6132922714999ce481c333edf84a6501645edc593ef239249af4f4"));

	// Zwei vorhandene Assets; ein drittes mit status planned geht nicht ein.
	const std::vector<ContentHashAsset> two {{"viewport", "viewport.png", a},
											 {"depth", "depth.png", b}};
	RTX_CHECK_EQ (CaptureContentHash (two).Value (),
				  std::string ("b206fbeb69755d266ed7600ced1f2e2d0c60578a0f0cd5fce311f9a54c36fcf6"));
}

RTX_TEST (ContentHashIstUnabhaengigVonDerReihenfolge)
{
	const std::string a (64, 'a');
	const std::string b (64, 'b');
	const std::vector<ContentHashAsset> forward {{"viewport", "viewport.png", a},
												 {"depth", "depth.png", b}};
	const std::vector<ContentHashAsset> backward {{"depth", "depth.png", b},
												  {"viewport", "viewport.png", a}};
	RTX_CHECK_EQ (CaptureContentHash (forward).Value (), CaptureContentHash (backward).Value ());
}

RTX_TEST (ContentHashLaesstAnzeigefelderUnberuehrt)
{
	CaptureManifest manifest = MakeManifest ();
	const std::string before = manifest.ContentHash ().Value ();
	manifest.viewDisplayName = "ein ganz anderer Name";
	manifest.projectDisplayName = "noch einer";
	RTX_CHECK_EQ (manifest.ContentHash ().Value (), before);
}

RTX_TEST (GueltigesManifestWirdAngenommen)
{
	const CaptureManifest manifest = MakeManifest ();
	RTX_CHECK (manifest.Validate ().IsOk ());
	const Result<std::string> text = manifest.Serialize ();
	RTX_CHECK (text.IsOk ());

	// Das Erzeugnis geht an `tools/check-manifest.mjs`: die native Prüfung des
	// Kerns und das geteilte Schema sind zwei Umsetzungen derselben Regeln, und
	// zwei Umsetzungen laufen auseinander, sobald niemand sie vergleicht.
	if (const char* sample = std::getenv ("RTX_MANIFEST_SAMPLE"))
		RTX_CHECK (WriteTextFile (sample, text.Value ()));
	RTX_CHECK (text.Value ().find ("\"contract\": \"rendertaxi.plugin.capture-manifest\"") !=
			   std::string::npos);
	// `camera` und `geometry` sind Pflicht und in v1 immer null.
	RTX_CHECK (text.Value ().find ("\"camera\": null") != std::string::npos);
	RTX_CHECK (text.Value ().find ("\"geometry\": null") != std::string::npos);
	// Kein Feld des Modellwegs (ADR 0009, Entscheidung 3).
	RTX_CHECK (text.Value ().find ("modelVersionId") == std::string::npos);
	RTX_CHECK (text.Value ().find ("predecessor") == std::string::npos);
	RTX_CHECK (text.Value ().find ("activation") == std::string::npos);
}

RTX_TEST (ManifestOhneVorhandenesAssetWirdAbgelehnt)
{
	CaptureManifest manifest = MakeManifest ();
	manifest.assets[0].status = "planned";
	manifest.assets[0].sha256.clear ();
	manifest.assets[0].byteSize = 0;
	manifest.assets[0].hasImage = false;
	RTX_CHECK (!manifest.Validate ());
	RTX_CHECK_EQ (manifest.Validate ().GetError ().code, std::string (errc::SchemaInvalid));
}

RTX_TEST (GeplantesAssetDarfKeinenHashTragen)
{
	CaptureManifest manifest = MakeManifest ();
	CaptureAsset beauty;
	beauty.role = "beauty";
	beauty.path = "beauty.png";
	beauty.status = "planned";
	beauty.sha256 = std::string (64, 'c');
	manifest.assets.push_back (beauty);
	RTX_CHECK (!manifest.Validate ());
}

RTX_TEST (DoppelteRolleWirdAbgelehnt)
{
	CaptureManifest manifest = MakeManifest ();
	CaptureAsset second = manifest.assets[0];
	second.path = "viewport-2.png";
	manifest.assets.push_back (second);
	RTX_CHECK (!manifest.Validate ());
}

RTX_TEST (DatenrollenVerlangenNonColor)
{
	CaptureManifest manifest = MakeManifest ();
	CaptureAsset depth = manifest.assets[0];
	depth.role = "depth";
	depth.path = "depth.png";
	depth.sha256 = std::string (64, 'd');
	depth.image.colorSpace = "srgb";
	manifest.assets.push_back (depth);
	RTX_CHECK (!manifest.Validate ());
}

RTX_TEST (PfadeMitElternsegmentWerdenAbgelehnt)
{
	CaptureManifest manifest = MakeManifest ();
	manifest.assets[0].path = "../viewport.png";
	RTX_CHECK (!manifest.Validate ());
	manifest.assets[0].path = "/viewport.png";
	RTX_CHECK (!manifest.Validate ());
}

RTX_TEST (UuidV7TraegtVersionUndVariante)
{
	const std::string id = NewUuidV7 ();
	RTX_CHECK (IsUuidV7 (id));
	RTX_CHECK_EQ (id.size (), std::size_t (36));
	// Zwei Aufrufe liefern verschiedene Kennungen.
	RTX_CHECK (NewUuidV7 () != NewUuidV7 ());
	// Der Zeitanteil steht vorn und ist monoton.
	std::uint8_t zeros[10] = {0};
	const std::string early = MakeUuidV7 (1000, zeros);
	const std::string late = MakeUuidV7 (2000, zeros);
	RTX_CHECK (early < late);
	RTX_CHECK (!IsUuidV7 ("0199A000-0000-7000-8000-000000000001"));   // Großschreibung
	RTX_CHECK (!IsUuidV7 ("0199a000-0000-4000-8000-000000000001"));   // Version 4
}

RTX_TEST (ZeitstempelHatMillisekundenUndZ)
{
	RTX_CHECK_EQ (FormatTimestampUtc (0), std::string ("1970-01-01T00:00:00.000Z"));
	RTX_CHECK_EQ (FormatTimestampUtc (1758362400123ULL), std::string ("2025-09-20T10:00:00.123Z"));
	RTX_CHECK (IsTimestampUtc (NowTimestampUtc ()));
	RTX_CHECK (!IsTimestampUtc ("2026-09-20T10:00:00Z"));
}

RTX_TEST (ProtokollEntferntGeheimnisse)
{
	RTX_CHECK (Redact ("Authorization: Bearer abc123").find ("abc123") == std::string::npos);
	RTX_CHECK (Redact (R"({"access_token":"geheim"})").find ("geheim") == std::string::npos);
	RTX_CHECK (Redact (R"({"device_code":"dc-99"})").find ("dc-99") == std::string::npos);
	RTX_CHECK (Redact (R"({"user_code":"WDJB-MJHT"})").find ("WDJB") == std::string::npos);
	const std::string signed_ = "https://store.example/org/p/ast/a?X-Amz-Signature=deadbeef";
	RTX_CHECK (Redact (signed_).find ("deadbeef") == std::string::npos);
	// Der unverfängliche Teil bleibt lesbar, sonst wäre die Fehlersuche blind.
	RTX_CHECK (Redact (signed_).find ("store.example/org/p/ast/a") != std::string::npos);
	RTX_CHECK_EQ (SafeUrl ("https://user:pw@example.test/a/b?c=d#e"),
				  std::string ("https://example.test/a/b?[redigiert]"));
}

RTX_TEST (JsonBleibtInDerEinfuegereihenfolge)
{
	JsonPtr node = Json::MakeObject ();
	node->Set ("b", Json::MakeInt (2));
	node->Set ("a", Json::MakeInt (1));
	RTX_CHECK_EQ (node->Serialize (), std::string ("{\"b\":2,\"a\":1}"));
	const JsonPtr parsed = Json::Parse (R"({"x":[1,2,{"y":"z"}],"n":null,"t":true})");
	RTX_CHECK (parsed != nullptr);
	RTX_CHECK_EQ (parsed->Get ("x")->Items ().size (), std::size_t (3));
	RTX_CHECK_EQ (parsed->Get ("x")->Items ()[2]->Get ("y")->StringOr (""), std::string ("z"));
	RTX_CHECK (parsed->Get ("n")->IsNull ());
	RTX_CHECK (parsed->Get ("t")->BoolOr (false));
	RTX_CHECK (Json::Parse ("{\"a\":}") == nullptr);
	// Maskierung: ein Tabulator im Text darf die Datei nicht zerreißen.
	RTX_CHECK_EQ (JsonQuote ("a\tb"), std::string ("\"a\\tb\""));
}

RTX_TEST (BildkopfWirdGelesenStattGeraten)
{
	const std::string directory = std::string (TransferStore::DefaultWorkDirectory ()) + "/selftest";
	RTX_CHECK (EnsureDirectory (directory));
	const std::string path = directory + "/tiny.png";

	// Ein PNG mit 3 x 2 Pixeln, 8 Bit, RGBA — nur Signatur und IHDR werden gelesen.
	std::string png ("\x89PNG\r\n\x1a\n", 8);
	const unsigned char ihdr[] = {0,    0,    0,    13,   'I',  'H',  'D',  'R', 0, 0, 0,
								  3,    0,    0,    0,    2,    8,    6,    0,   0, 0, 0,
								  0,    0,    0};
	png.append (reinterpret_cast<const char*> (ihdr), sizeof ihdr);
	RTX_CHECK (WriteTextFile (path, png));

	const Result<ImageInfo> info = ReadImageInfo (path);
	RTX_CHECK (info.IsOk ());
	RTX_CHECK_EQ (info.Value ().width, 3);
	RTX_CHECK_EQ (info.Value ().height, 2);
	RTX_CHECK_EQ (info.Value ().bitDepth, 8);
	RTX_CHECK_EQ (info.Value ().channels, std::string ("rgba"));
	RTX_CHECK_EQ (info.Value ().mediaType, std::string ("image/png"));

	// Der Dateihash stimmt mit dem über den Inhalt gebildeten überein.
	bool ok = false;
	RTX_CHECK_EQ (Sha256OfFile (path, &ok), Sha256::OfString (png));
	RTX_CHECK (ok);
	RemoveDirectory (directory);
}

// --- Bildformat: die Plattform führt ----------------------------------------

RTX_TEST (AusgabezielWirdAusDemRezeptGelesen)
{
	const DesiredOutput ratio = ParseDesiredOutput (
		Json::Parse (R"({"kind":"aspect_ratio","value":"16:9"})"));
	RTX_CHECK (ratio.known);
	RTX_CHECK_EQ (ratio.aspectWidth, 16);
	RTX_CHECK_EQ (ratio.aspectHeight, 9);
	RTX_CHECK_EQ (ratio.label, std::string ("16:9"));

	const DesiredOutput exact =
		ParseDesiredOutput (Json::Parse (R"({"kind":"exact","width":1920,"height":1080})"));
	RTX_CHECK (exact.known);
	RTX_CHECK_EQ (exact.exactWidth, 1920);
	RTX_CHECK_EQ (exact.exactHeight, 1080);
	// Das Verhältnis wird gekürzt mitgeführt: 1920 x 1080 ist 16:9.
	RTX_CHECK_EQ (exact.aspectWidth, 16);
	RTX_CHECK_EQ (exact.aspectHeight, 9);

	// `null` und Unsinn ergeben „unbekannt", nicht ein erfundenes Format.
	RTX_CHECK (!ParseDesiredOutput (Json::Parse ("null")).known);
	RTX_CHECK (!ParseDesiredOutput (nullptr).known);
	RTX_CHECK (!ParseDesiredOutput (Json::Parse (R"({"kind":"aspect_ratio","value":"16"})")).known);
	RTX_CHECK (!ParseDesiredOutput (Json::Parse (R"({"kind":"exact","width":0,"height":9})")).known);
}

RTX_TEST (ZuschnittTrifftDasZielformatUndSchneidetMittig)
{
	// Grundlage ist eine echte Aufnahme aus Archicad 28 (1071 x 905), falls sie
	// noch liegt; sonst genügt jedes andere PNG dieser Prüfung nicht und sie
	// wird übersprungen — sie prüft den Zuschnitt, nicht die Aufnahme.
	const std::string directory = TransferStore::DefaultWorkDirectory () + "/crop-selftest";
	RTX_CHECK (EnsureDirectory (directory));

	// Ein reales PNG erzeugen, indem ein vorhandenes zugeschnitten wird: der
	// Zuschnitt schreibt PNG, also liefert er auch die Vorlage.
	const std::string source = directory + "/source.png";
	const std::string target = directory + "/target.png";

	// 1071 x 905 mit einem einfachen Muster — über ImageIO geschrieben, indem
	// ein größeres Bild auf genau diese Maße geschnitten wird, ist zirkulär.
	// Stattdessen wird die Quelle aus dem Bildkopf-Selbsttest wiederverwendet:
	// ein 4 x 3 grosses, gültiges PNG aus dem Zuschnitt selbst.
	// Erzeugt wird sie hier einmal über den Zuschnitt eines Graustufenbildes.
	// Fällt das aus, ist ImageIO nicht verfügbar und die Prüfung endet.
	{
		// Minimales, gültiges PNG: 2 x 2, 8 Bit RGBA, unkomprimierter Deflate-Block.
		static const unsigned char tiny[] = {
			0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
			0x44, 0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x02, 0x08, 0x06, 0x00, 0x00,
			0x00, 0x72, 0xB6, 0x0D, 0x24, 0x00, 0x00, 0x00, 0x16, 0x49, 0x44, 0x41, 0x54, 0x78,
			0x01, 0x01, 0x0B, 0x00, 0xF4, 0xFF, 0x00, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00,
			0x00, 0xFF, 0xFF, 0x0B, 0xF8, 0x02, 0xFE, 0x33, 0x6C, 0x6A, 0x2A, 0x00, 0x00, 0x00,
			0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82};
		RTX_CHECK (WriteTextFile (source, std::string (reinterpret_cast<const char*> (tiny),
													   sizeof tiny)));
	}

	// Unbekanntes Verhältnis: nichts zu tun, und das ist kein Fehler.
	const Result<CropResult> untouched = CropImageToAspect (source, target, 0, 0);
	RTX_CHECK (untouched.IsOk ());
	RTX_CHECK (!untouched.Value ().cropped);

	// 2 x 2 ist bereits 1:1 — ebenfalls nichts zu tun.
	const Result<CropResult> square = CropImageToAspect (source, target, 1, 1);
	RTX_CHECK (square.IsOk ());
	RTX_CHECK (!square.Value ().cropped);

	// 2 x 2 auf 2:1: die Höhe wird halbiert, die Breite bleibt.
	const Result<CropResult> wide = CropImageToAspect (source, target, 2, 1);
	RTX_CHECK (wide.IsOk ());
	RTX_CHECK (wide.Value ().cropped);
	RTX_CHECK_EQ (wide.Value ().width, 2);
	RTX_CHECK_EQ (wide.Value ().height, 1);

	// Das Ergebnis ist ein lesbares PNG mit genau diesen Maßen.
	const Result<ImageInfo> info = ReadImageInfo (target);
	RTX_CHECK (info.IsOk ());
	RTX_CHECK_EQ (info.Value ().width, 2);
	RTX_CHECK_EQ (info.Value ().height, 1);
	RTX_CHECK_EQ (info.Value ().mediaType, std::string ("image/png"));

	// Derselbe Zuschnitt zweimal ergibt dieselben Bytes — Voraussetzung für die
	// Deduplizierung über SHA-256.
	bool ok = false;
	const std::string first = Sha256OfFile (target, &ok);
	RTX_CHECK (ok);
	RTX_CHECK (CropImageToAspect (source, target, 2, 1).IsOk ());
	RTX_CHECK_EQ (Sha256OfFile (target, &ok), first);

	RemoveDirectory (directory);
}
