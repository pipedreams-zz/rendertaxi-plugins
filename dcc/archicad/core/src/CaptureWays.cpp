#include "rtx/CaptureWays.hpp"

#include <algorithm>

namespace rtx {

int HighestCaptureMinor (const HandshakeInfo& handshake)
{
	if (handshake.negotiationContract != kCaptureContract) return -1;
	const std::string& version = handshake.highestSupportedVersion;
	const int minor = ContractMinor (version);
	return minor;
}

std::string ImageContractVersion (int minor)
{
	if (minor >= 6) return "1.6.0";
	if (minor >= 5) return "1.5.0";
	if (minor >= 3) return "1.3.0";
	if (minor >= 1) return "1.1.0";
	return "1.0.0";
}

std::string ModelContractVersion (int minor)
{
	if (minor >= kCaptureHighestMinor) return "1." + std::to_string (kCaptureHighestMinor) + ".0";
	if (minor >= 2) return "1." + std::to_string (minor) + ".0";
	return {};
}

Result<CapturePlan> PlanCapture (bool sendImage, bool sendModel, int minor)
{
	if (!sendImage && !sendModel) return Result<CapturePlan>::Fail (errc::SchemaInvalid, kNothingChosen);
	CapturePlan plan;
	plan.image = sendImage;
	plan.model = sendModel;
	if (minor < 0) return Result<CapturePlan>::Ok (plan);
	if (plan.model && minor < 2) {
		plan.model = false;
		plan.image = true;
		plan.hint = kModelUnsupported;
	} else if (plan.ModelOnly () && minor < 6) {
		plan.image = true;
		plan.hint = kModelOnlyFallback;
	}
	return Result<CapturePlan>::Ok (plan);
}

std::string PlanContractVersion (const CapturePlan& plan, int minor)
{
	if (plan.model) {
		const std::string version = ModelContractVersion (minor < 0 ? 2 : minor);
		if (!version.empty ()) return version;
	}
	return ImageContractVersion (minor);
}

std::string PlanLabel (const CapturePlan& plan)
{
	if (plan.ModelOnly ()) return "Modell";
	return plan.model ? "Bild und Modell" : "Bild";
}

std::string PlanSummary (const CapturePlan& plan)
{
	if (plan.ModelOnly ()) return "Gesendet wird: nur Modell und Kamera — ohne Rendern.";
	if (plan.model) return "Gesendet werden: Bild und Modell.";
	return "Gesendet wird: nur Bild.";
}

std::vector<std::string> PlanSteps (const CapturePlan& plan)
{
	std::vector<std::string> steps;
	if (plan.image) steps.push_back ("Bild aufnehmen");
	if (plan.model) {
		steps.push_back ("Modell exportieren");
		steps.push_back ("Kamera lesen");
	}
	if (plan.image) steps.push_back ("Bild übertragen");
	if (plan.model) steps.push_back ("Modell übertragen");
	steps.push_back ("Übernahme abschließen");
	return steps;
}

std::string PlanResultText (const CaptureResult& result, bool update)
{
	bool image = false, model = false;
	for (const auto& [role, id] : result.assetIdsByRole) {
		if (role == kModelRole)
			model = true;
		else
			image = true;
	}
	if (model && !image)
		return std::string ("Modell und Kamera übernommen.") + (update ? " Ein Bild am Blickpunkt bleibt, wie es ist." : "");
	if (model) return "Bild und Modell übernommen.";
	if (image) return std::string ("Bild übernommen.") + (update ? " Ein Modell am Blickpunkt bleibt, wie es ist." : "");
	return "Übernahme abgeschlossen.";
}

} // namespace rtx
