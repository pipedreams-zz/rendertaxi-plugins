#include "rtx/Numbers.hpp"

namespace rtx {

bool ParseBoundedInt (const std::string& text, int& value, int max)
{
	if (text.empty () || max < 0) return false;
	long long accumulated = 0;
	for (const char c : text) {
		if (c < '0' || c > '9') return false;
		accumulated = accumulated * 10 + (c - '0');
		// Vor dem nächsten Schritt abbrechen: so bleibt `accumulated` immer unter 10 · max + 9.
		if (accumulated > max) return false;
	}
	value = static_cast<int> (accumulated);
	return true;
}

} // namespace rtx
