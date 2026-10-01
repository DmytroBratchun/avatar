def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[int, float]]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Combine several (scene_id, distance) rankings into one fused ranking.

    Each input list must already be sorted best-first. Uses reciprocal rank
    fusion so lists with different distance scales (text vs. visual) combine
    without needing to normalize scores.
    """
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (scene_id, _distance) in enumerate(ranked):
            scores[scene_id] = scores.get(scene_id, 0.0) + 1.0 / (k + rank + 1)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
