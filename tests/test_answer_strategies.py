import pytest

from playstealth_actions.answer_strategies import get_strategy


@pytest.mark.asyncio
async def test_consistent_strategy_uses_fixed_index() -> None:
    strategy = get_strategy("consistent", fixed_index=1)
    chosen = await strategy.choose("Question", 3, ["A", "B", "C"])
    assert chosen == 1


@pytest.mark.asyncio
async def test_persona_strategy_prefers_non_smoker_choice() -> None:
    strategy = get_strategy("persona", persona="default")
    chosen = await strategy.choose(
        "Welche der Zigarettenmarken rauchen Sie hauptsächlich?",
        2,
        ["Marlboro", "Ich rauche keine Zigaretten"],
    )
    assert chosen == 1
