#include "Testing.hpp"

namespace testing {
namespace {

int failures = 0;
std::string currentTest;

} // namespace

std::vector<TestCase>& Registry ()
{
	static std::vector<TestCase> registry;
	return registry;
}

void Fail (const std::string& file, int line, const std::string& message)
{
	++failures;
	std::cerr << "  FEHLER " << currentTest << " (" << file << ":" << line << "): " << message
			  << "\n";
}

int RunAll ()
{
	int failed = 0;
	for (const TestCase& test : Registry ()) {
		currentTest = test.name;
		const int before = failures;
		test.body ();
		if (failures > before) {
			++failed;
			std::cout << "nicht bestanden  " << test.name << "\n";
		} else {
			std::cout << "bestanden        " << test.name << "\n";
		}
	}
	std::cout << "\n" << Registry ().size () << " Prüfungen, " << failed << " nicht bestanden\n";
	return failed == 0 ? 0 : 1;
}

} // namespace testing

int main ()
{
	return testing::RunAll ();
}
