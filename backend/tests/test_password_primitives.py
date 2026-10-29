import pytest

from app.passwords import hash_password, verify_password
from app.secrets import read_secret


def test_password_salts_verification_and_fixed_cost_format():
    password = "A strong fixture password 你好"
    first, second = hash_password(password), hash_password(password)
    assert first != second and password not in first
    assert verify_password(password, first) and not verify_password("bad", first)
    assert not verify_password(password, "scrypt$999999999$bad")


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_invalid_password_lengths(password):
    with pytest.raises(ValueError):
        hash_password(password)


def test_secret_file_is_explicit_and_cannot_conflict(tmp_path):
    path = tmp_path / "secret"
    path.write_text("fixture-secret\n", encoding="utf-8")
    assert read_secret("PW", {"PW_FILE": str(path)}) == "fixture-secret"
    with pytest.raises(ValueError):
        read_secret("PW", {"PW": "other", "PW_FILE": str(path)})
    with pytest.raises(ValueError):
        read_secret("PW", {"PW_FILE": "relative-secret"})
    with pytest.raises(ValueError):
        read_secret("PW", {})
