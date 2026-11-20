from dataclasses import dataclass


@dataclass(frozen=True)
class LimitDecision:
    limit_microliters: int
    over_limit: bool


def decide_limit(microliters: int, limit_microliters: int) -> LimitDecision:
    return LimitDecision(limit_microliters, microliters > limit_microliters)
