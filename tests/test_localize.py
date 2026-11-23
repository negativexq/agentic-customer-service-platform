import pytest

from app.agent.localize import detect_language, localize_reply

REPLY = "You have 2 orders: #1: delivered, $149.99; #2: shipped, $39.50."


class _Fixed:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def translate(self, reply: str, language: str) -> str:
        self.calls += 1
        assert language == "Turkish"
        return self.text


class _Broken:
    def translate(self, reply: str, language: str) -> str:
        raise TimeoutError


@pytest.mark.parametrize(
    ("message", "language"),
    [
        ("Siparişlerimi gösterir misin?", "Turkish"),
        ("merhaba", "Turkish"),
        ("kargo ne zaman gelir", "Turkish"),
        ("I need help.", None),
        ("Is order 5 cancelled?", None),
    ],
)
def test_language_detection(message: str, language: str | None) -> None:
    assert detect_language(message) == language


def test_turkish_customer_gets_translated_reply() -> None:
    translated = "2 siparişiniz var: #1: teslim edildi, $149.99; #2: kargoda, $39.50."
    assert localize_reply(_Fixed(translated), "Siparişlerim?", REPLY) == translated


def test_translation_that_changes_a_number_is_rejected() -> None:
    wrong = "2 siparişiniz var: #1: teslim edildi, $149.99; #3: kargoda, $39.50."
    assert localize_reply(_Fixed(wrong), "Siparişlerim?", REPLY) == REPLY


def test_english_and_failures_keep_the_english_reply() -> None:
    localizer = _Fixed("unused")
    assert localize_reply(localizer, "Show my orders", REPLY) == REPLY
    assert localizer.calls == 0
    assert localize_reply(_Broken(), "Siparişlerim?", REPLY) == REPLY
    assert localize_reply(None, "Siparişlerim?", REPLY) == REPLY


def test_reply_already_in_turkish_is_not_sent_for_translation() -> None:
    localizer = _Fixed("unused")
    turkish = "Merhaba! Size nasıl yardımcı olayım?"
    assert localize_reply(localizer, "Merhaba", turkish) == turkish
    assert localizer.calls == 0
