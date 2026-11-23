"""Hello, chit-chat, goodbye and off-topic questions get short human replies, not a search."""

from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.agent.compound import read_message
from app.agent.llm.fake import FakeSemanticDecisionV3Provider
from app.agent.nodes.retrieve_knowledge import _names_what_the_article_lacks
from app.agent.runtime import HANDOFF_ACKNOWLEDGEMENT_TR, AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, SemanticDecisionV3
from app.rag.schemas import RetrievedChunk


class MoveReader:
    """A model reading that marks the message as one conversation move."""

    def __init__(self, move: str | None, **signals: object) -> None:
        self.move = move
        self.signals = signals

    def read(self, message: str) -> dict[str, object]:
        return {"requests": [{"text": message}], "conversation_move": self.move, **self.signals}


class NoRetrieval:
    def retrieve(self, query: str) -> object:
        raise AssertionError(f"a conversation move must not search the articles: {query}")


def _say(runtime: AgentRuntime, session: Session, conversation: str, message: str):
    return runtime.run(
        conversation_id=conversation, customer_id=1, message=message, session=session
    )


@pytest.mark.parametrize(
    ("move", "message", "expected"),
    [
        ("greeting", "Günaydın!", "How can I help you today?"),
        ("small_talk", "how are you today?", "Thanks for chatting!"),
        ("closing", "thanks, bye!", "Have a great day"),
        ("off_topic", "Can you give me a cake recipe?", "I can't help with that here"),
        ("about_assistant", "what's your name?", "I'm the store's AI assistant"),
    ],
)
def test_conversation_moves_skip_the_decision_and_the_articles(
    db_session: Session, move: str, message: str, expected: str
) -> None:
    provider = FakeSemanticDecisionV3Provider([])
    runtime = AgentRuntime(
        provider=provider, reader=MoveReader(move), knowledge_retriever=NoRetrieval()
    )

    response = _say(runtime, db_session, f"move-{move}", message)

    assert provider.calls == []
    assert expected in response.message
    if move in {"off_topic", "closing"}:
        assert "open a support ticket" not in response.message


def test_closing_outranks_a_plain_acknowledgement(db_session: Session) -> None:
    runtime = AgentRuntime(
        provider=FakeSemanticDecisionV3Provider([]),
        reader=MoveReader("closing", acknowledgement=True),
    )

    response = _say(runtime, db_session, "move-closing-ack", "ok thanks, have a nice day")

    assert "Have a great day" in response.message
    assert "anything else I can help with?" not in response.message


def test_the_customer_hears_their_own_account_name(db_session: Session) -> None:
    runtime = AgentRuntime(
        provider=FakeSemanticDecisionV3Provider([]), reader=MoveReader("own_name")
    )

    response = _say(runtime, db_session, "move-own-name", "do you know my name?")

    assert "Test Customer" in response.message
    assert "email" not in response.message.casefold()  # memories are not the answer


def test_an_unexpected_move_label_keeps_the_rest_of_the_reading() -> None:
    class Reader:
        def read(self, message: str) -> dict[str, object]:
            return {"requests": [{"text": message}], "upset": True, "conversation_move": "rant"}

    reading = read_message(Reader(), "this is the third time!!")
    assert reading.upset is True and reading.conversation_move is None


def test_a_business_request_still_reaches_the_decision(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [SemanticDecisionV3(intent=Intent.ORDER_LIST, request_type=AgentRequestType.READ_ACTION)]
    )
    runtime = AgentRuntime(provider=provider, reader=MoveReader(None))

    response = _say(runtime, db_session, "move-none", "hi, what are my orders?")

    assert len(provider.calls) == 1
    assert response.intent == Intent.ORDER_LIST


def test_recap_lists_what_was_done_once_and_skips_empty_turns(db_session: Session) -> None:
    provider = FakeSemanticDecisionV3Provider(
        [
            SemanticDecisionV3(
                intent=Intent.ORDER_LOOKUP,
                request_type=AgentRequestType.READ_ACTION,
                target={"type": "explicit_order", "order_id": 1},
            ),
            SemanticDecisionV3(
                intent=Intent.ORDER_LOOKUP,
                request_type=AgentRequestType.READ_ACTION,
                target={"type": "explicit_order", "order_id": 1},
            ),
            SemanticDecisionV3(intent=Intent.UNKNOWN, request_type=AgentRequestType.UNCLEAR),
        ]
    )
    runtime = AgentRuntime(provider=provider)
    first = _say(runtime, db_session, "recap-covered", "Where is order 1?")
    _say(runtime, db_session, "recap-covered", "And order 1 again?")
    runtime.reader = MoveReader("greeting")
    _say(runtime, db_session, "recap-covered", "hello")
    runtime.reader = None

    recap = _say(runtime, db_session, "recap-covered", "What did we talk about?").message

    lead = first.message.split(". ")[0].rstrip(".")
    assert recap.count(lead) == 1
    assert "Hi!" not in recap


def test_a_queued_conversation_answers_in_the_customers_language(db_session: Session) -> None:
    from test_conversation_handoff import _escalate

    _escalate(db_session, "handoff-turkish")
    response = AgentRuntime(provider=FakeSemanticDecisionV3Provider([])).run(
        conversation_id="handoff-turkish",
        customer_id=1,
        message="Talep numarası neydi?",
        session=db_session,
    )

    assert response.message == HANDOFF_ACKNOWLEDGEMENT_TR


def _chunk(content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="shipping-policy#processing#0",
        document_id="shipping-policy",
        title="Shipping Policy",
        category="shipping",
        section="Processing",
        source="knowledge/shipping-policy.md",
        content=content,
        score=1.0,
    )


def test_a_question_naming_what_the_article_lacks_is_not_answered_from_it() -> None:
    article: Sequence[RetrievedChunk] = [
        _chunk("Orders typically spend one to three business days in processing before carrier.")
    ]
    assert _names_what_the_article_lacks("do you ship internationally?", article)
    assert _names_what_the_article_lacks("how much is shipping?", article)
    assert not _names_what_the_article_lacks("can I return it if I don't like it?", article)
    # A vague follow-up shares no words with the article; its classified topic answers it.
    assert not _names_what_the_article_lacks("ne zaman gelir?", article)
    assert not _names_what_the_article_lacks("Bu kadar uzun sürmesi normal mi?", article)
