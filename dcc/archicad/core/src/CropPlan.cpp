#include "rtx/ImageCrop.hpp"

#include <cmath>

namespace rtx {

CropPlan PlanCrop (int width, int height, int aspectWidth, int aspectHeight)
{
	CropPlan plan;
	plan.width = width;
	plan.height = height;
	if (aspectWidth <= 0 || aspectHeight <= 0 || width <= 0 || height <= 0) return plan;

	const double wanted = static_cast<double> (aspectWidth) / static_cast<double> (aspectHeight);
	const double actual = static_cast<double> (width) / static_cast<double> (height);
	// Ein halbes Prozent Abweichung ist keinen Zuschnitt wert: das Rezept
	// beschreibt ein Verhältnis, keine Pixelgrenze.
	if (std::fabs (actual - wanted) / wanted < 0.005) return plan;

	int cropWidth = width;
	int cropHeight = height;
	if (actual > wanted) {
		cropWidth = static_cast<int> (static_cast<double> (height) * wanted + 0.5);
		if (cropWidth > width) cropWidth = width;
	} else {
		cropHeight = static_cast<int> (static_cast<double> (width) / wanted + 0.5);
		if (cropHeight > height) cropHeight = height;
	}
	if (cropWidth < 1 || cropHeight < 1) return plan;

	plan.needed = true;
	plan.x = (width - cropWidth) / 2;
	plan.y = (height - cropHeight) / 2;
	plan.width = cropWidth;
	plan.height = cropHeight;
	return plan;
}

} // namespace rtx
