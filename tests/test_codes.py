import pytest

from jpstock.codes import InvalidCodeError, parse_codes


def test_parse_multiple_codes():
    assert parse_codes("7203,6758, 9984") == ["7203", "6758", "9984"]


def test_parse_normalizes_fullwidth_five_digit_and_duplicates():
    assert parse_codes("７２０３，72030,130a,") == ["7203", "130A"]


@pytest.mark.parametrize("text", ["", " , ", "abc", "12345", "7203;6758"])
def test_parse_invalid(text):
    with pytest.raises(InvalidCodeError):
        parse_codes(text)
