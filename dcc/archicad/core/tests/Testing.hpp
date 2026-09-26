// Winziger Testlauf ohne Fremdbibliothek.
//
// Der Kern soll auf jedem Mac ohne Vorbereitung prüfbar sein; ein Testrahmen
// als zusätzliche Abhängigkeit widerspräche der Reiferichtlinie aus
// `AGENTS.md` für einen Nutzen, den dreißig Zeilen auch erbringen.
#pragma once

#include <functional>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace testing {

struct TestCase {
	std::string name;
	std::function<void ()> body;
};

std::vector<TestCase>& Registry ();
void Fail (const std::string& file, int line, const std::string& message);

struct Registrar {
	Registrar (const std::string& name, std::function<void ()> body)
	{
		Registry ().push_back (TestCase {name, std::move (body)});
	}
};

int RunAll ();

} // namespace testing

#define RTX_TEST(name)                                                                   \
	static void name ();                                                                 \
	static testing::Registrar registrar_##name (#name, name);                            \
	static void name ()

#define RTX_CHECK(condition)                                                             \
	do {                                                                                 \
		if (!(condition)) testing::Fail (__FILE__, __LINE__, "erwartet: " #condition);    \
	} while (false)

#define RTX_CHECK_EQ(actual, expected)                                                   \
	do {                                                                                 \
		/* Bewusst Kopien: ein `Result<T>::Value ()` auf einem Temporary würde      \
		   sonst eine Referenz auf zerstörten Speicher binden. */                        \
		const auto rtxActual = (actual);                                                 \
		const auto rtxExpected = (expected);                                             \
		if (!(rtxActual == rtxExpected)) {                                               \
			std::ostringstream rtxMessage;                                               \
			rtxMessage << #actual << " ist " << rtxActual << ", erwartet " << rtxExpected; \
			testing::Fail (__FILE__, __LINE__, rtxMessage.str ());                       \
		}                                                                                \
	} while (false)
