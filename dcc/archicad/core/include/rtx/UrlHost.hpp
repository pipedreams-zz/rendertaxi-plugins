// Die Autorität einer URL — **gelesen vom System, nicht selbst zerlegt**.
//
// Warum es diese Datei gibt: der erste Anlauf der Transportregel zerlegte die
// Adresse mit `find` und `substr`. Er hielt `http://localhost:80@evil.example/`
// für die Schleife, weil er vor dem ersten `:` abschnitt — tatsächlich ist
// `localhost:80` dort die **Benutzerangabe** und `evil.example` der Host. Ein
// Anmeldetoken wäre im Klartext an einen fremden Rechner gegangen.
//
// Die Lehre ist nicht „den Parser reparieren", sondern **keinen eigenen
// schreiben**. Auf macOS beantwortet CFURL genau diese Frage; die Regeln von
// RFC 3986 stehen dort seit Jahren richtig.
#pragma once

#include <string>

namespace rtx {

/** Die Teile einer Adresse, auf die es für die Transportregel ankommt. */
struct UrlParts {
	/** Falsch, wenn das System die Adresse nicht als URL annimmt. */
	bool valid = false;
	/** Kleingeschrieben, ohne `://`. */
	std::string scheme;
	/** Der **Host**, wie der Systemparser ihn liest; ohne Port und Klammern. */
	std::string host;
	/**
	 * Trägt die Adresse eine Benutzerangabe (`user:pass@`)?
	 *
	 * Sie ist in einer Serveradresse nie erwünscht und wird abgelehnt, auch
	 * wenn der Host danach harmlos aussieht.
	 */
	bool hasUserInfo = false;
};

/** Zerlegt eine Adresse mit dem Parser des Systems. */
UrlParts ParseUrl (const std::string& url);

} // namespace rtx
