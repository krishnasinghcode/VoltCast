"""Risk derivation (mlMaster.md ss6.3) — NOT ML, a product-policy rule on top of the
prediction. Thresholds are project/demo policy, not an industry standard."""
from . import config


def risk_level(predicted_degradation_30d_pct: float) -> str:
    if predicted_degradation_30d_pct >= config.RISK_THRESHOLD_HIGH:
        return "HIGH"
    elif predicted_degradation_30d_pct >= config.RISK_THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "LOW"
