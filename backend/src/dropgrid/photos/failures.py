"""Deterministic, non-sensitive preparation failure codes."""


def reason(pool_size: int, warnings: list[str], cooldown: int = 0, reuse: int = 0) -> str:
    if any("storage" in w for w in warnings):
        return "storage_error"
    if any(
        w
        in {
            "download_failed",
            "download_http_error",
            "download_timeout",
            "download_destination_rejected",
        }
        for w in warnings
    ):
        return "download_error"
    if any(w.startswith("category_archive_") for w in warnings):
        return "category_archive_error"
    if not pool_size:
        return "no_candidates"
    if cooldown >= pool_size:
        return "cooldown_exhausted"
    if reuse >= pool_size or "duplicate_not_reusable" in warnings:
        return "dedup_exhausted"
    if any(w in {"no_compatible_core_references", "references_unavailable"} for w in warnings):
        return "no_references"
    if any(
        w
        in {
            "candidate_unavailable",
            "archive_candidate_unavailable",
            "image_invalid",
            "image_too_small",
        }
        for w in warnings
    ):
        return "materialization_failed"
    if "pinterest_search_unavailable" in warnings:
        return "pinterest_unavailable"
    return "other"
