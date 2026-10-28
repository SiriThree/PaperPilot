import inspect
from typing import Literal

import pytest

from signature_contracts import assert_signature_matches


def test_optional_literal_matches_real_runtime_signature():
    def role(user_id: str, project_id: str) -> Literal["owner", "member"] | None:
        pass

    assert_signature_matches(
        "(user_id: str, project_id: str) -> Optional[Literal['owner', 'member']]", inspect.signature(role)
    )


@pytest.mark.parametrize(
    "expected,actual",
    [
        ("(x: Optional[int]) -> None", "(x: int | None) -> None"),
        ("(x: typing.Optional[int]) -> None", "(x: None | int) -> None"),
        ("(x: Union[int, str, None])", "(x: str | int | None)"),
        ("(x: typing.Union[int, str])", "(x: str | int)"),
        ("(x: Optional[Union[int, str]])", "(x: str | None | int)"),
        (
            "(x: list[Optional[int]]) -> dict[str, Optional[str]]",
            "(x: list[int | None]) -> dict[str, str | None]",
        ),
        ("(x: int | int | None)", "(x: Optional[int])"),
        ("(x: Optional[int] = None, *, flag: bool = False)", "(x: int | None=None, *, flag: bool=False)"),
    ],
)
def test_equivalent_typing_syntax_matches(expected, actual):
    assert_signature_matches(expected, actual)


@pytest.mark.parametrize(
    "expected,actual",
    [
        ("(x: int)", "(renamed: int)"),
        ("(x: int, y: str)", "(y: str, x: int)"),
        ("(x: int)", "(*, x: int)"),
        ("(x: int, /)", "(x: int)"),
        ("(x: int = 1)", "(x: int = 2)"),
        ("(x: int)", "(x: int = 1)"),
        ("(x: int)", "(x: str)"),
        ("(x: int)", "(x: Optional[int])"),
        ("(x: Optional[int])", "(x: int | str | None)"),
        ("(x: list[int | None])", "(x: list[int] | None)"),
        ("(x: int) -> int", "(x: int) -> str"),
        ("(x: int)", "(x: int) -> None"),
        ("(*args: int)", "(**args: int)"),
        ("(x: Literal['Optional[int]'])", "(x: Literal['int | None'])"),
        ("(x: Literal['owner', 'member'])", "(x: Literal['owner', 'admin'])"),
        ("(x: custom.Optional[int])", "(x: int | None)"),
        ("(x = 'Optional[int]')", "(x = 'int | None')"),
    ],
)
def test_real_contract_changes_are_rejected(expected, actual):
    with pytest.raises(AssertionError, match="Contract signature changed"):
        assert_signature_matches(expected, actual)
