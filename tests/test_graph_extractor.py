from aegis_core.memory.graph_extractor import DeterministicGraphExtractor


def test_extractor_builds_bounded_deterministic_entities_and_relationships() -> None:
    extractor = DeterministicGraphExtractor()
    text = "El proyecto Jarvis usa Python. Python requiere SQLite y utiliza LangGraph."

    first = extractor.extract(text)
    second = extractor.extract(text)

    assert first == second
    assert {(entity.type, entity.name) for entity in first.entities} >= {
        ("project", "Jarvis"),
        ("tool", "Python"),
        ("tool", "SQLite"),
        ("tool", "LangGraph"),
    }
    assert {(edge.subject.name, edge.type, edge.object.name) for edge in first.relationships} >= {
        ("Jarvis", "USES", "Python"),
        ("Python", "REQUIRES", "SQLite"),
    }


def test_extractor_rejects_urls_emails_and_control_characters() -> None:
    extractor = DeterministicGraphExtractor()

    assert extractor.extract("Jarvis usa https://example.invalid").entities == ()
    assert extractor.extract("Jarvis usa owner@example.invalid").entities == ()
    assert extractor.extract("Jarvis\x00 usa Python").entities == ()
