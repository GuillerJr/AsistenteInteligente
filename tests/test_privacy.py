import pytest

from aegis_core.privacy import redact_for_remote, sensitive_topics


def test_redact_for_remote_removes_credentials_and_personal_identifiers() -> None:
    result = redact_for_remote(
        "Bearer abcdefghijklmnop; token=secret-value; owner@example.com; "
        "+593 99 123 4567; /Users/owner/private"
    )

    assert result.text == (
        "Bearer [REDACTED_CREDENTIAL]; token=[REDACTED_SECRET]; [REDACTED_EMAIL]; "
        "[REDACTED_PHONE]; /Users/[REDACTED_USER]/private"
    )
    assert result.categories == frozenset(
        {"authorization", "secret_assignment", "email", "phone", "local_user"}
    )


def test_redact_for_remote_preserves_network_addresses_and_normal_code() -> None:
    text = "Escanea 192.168.1.0/24 y revisa value = 42"

    result = redact_for_remote(text)

    assert result.text == text
    assert result.categories == frozenset()


@pytest.mark.parametrize(
    ("text", "expected", "category"),
    [
        ("Mi tarjeta 4111 1111 1111 1111", "Mi tarjeta [REDACTED_CARD]", "payment_card"),
        ("pago con 5500-0000-0000-0004", "pago con [REDACTED_CARD]", "payment_card"),
        ("IBAN ES9121000418450200051332", "IBAN [REDACTED_BANK_ACCOUNT]", "bank_account"),
        ("cédula 1710034065", "cédula [REDACTED_NATIONAL_ID]", "national_id"),
        ("RUC 1710034065001", "RUC [REDACTED_NATIONAL_ID]", "national_id"),
        ("SSN 123-45-6789", "SSN [REDACTED_NATIONAL_ID]", "national_id"),
        ("DNI 12345678Z", "DNI [REDACTED_NATIONAL_ID]", "national_id"),
        ("llámame al 0991234567", "llámame al [REDACTED_PHONE]", "phone"),
        ("whatsapp +593991234567", "whatsapp [REDACTED_PHONE]", "phone"),
        ("estoy en -2.170998, -79.922359", "estoy en [REDACTED_LOCATION]", "location"),
        (
            "postgres://admin:hunter22@db.local/app",
            "postgres://[REDACTED_CREDENTIAL]@db.local/app",
            "url_credentials",
        ),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
            "jwt [REDACTED_CREDENTIAL]",
            "credential",
        ),
        ("key sk-ant-api03-abcdefghijkl", "key [REDACTED_CREDENTIAL]", "credential"),
        ("stripe sk_live_abcdefghijkl", "stripe [REDACTED_CREDENTIAL]", "credential"),
        ("hf hf_abcdefghijklmnopqrstu", "hf [REDACTED_CREDENTIAL]", "credential"),
        ("gitlab glpat-abcdefghijkl", "gitlab [REDACTED_CREDENTIAL]", "credential"),
        ("ruta /home/guille/x", "ruta /home/[REDACTED_USER]/x", "local_user"),
        ("ruta C:\\Users\\guille\\docs", "ruta C:\\Users\\[REDACTED_USER]\\docs", "local_user"),
    ],
)
def test_redact_for_remote_covers_identifiers_and_tokens(
    text: str, expected: str, category: str
) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({category})


@pytest.mark.parametrize(
    "text",
    [
        "Reunión el 2026-10-16 a las 10:30:45",
        "versión 1.2.3, commit 3f2a9c1d y tag v2026.10.16",
        "puerto 8080, pid 12345 y 443/tcp",
        "timestamp 1729100000000 y orden 1234567890123",
        "id 550e8400-e29b-41d4-a716-446655440000",
        "red 10.0.0.0/8 y host 172.16.4.20:5432",
        "tarjeta inválida 4111 1111 1111 1112",
        "cédula inválida 1710034066",
        "coordenada fuera de rango 95.123456, 200.123456",
    ],
)
def test_redact_for_remote_keeps_technical_and_invalid_values(text: str) -> None:
    result = redact_for_remote(text)

    assert result.text == text
    assert result.categories == frozenset()


def test_url_credentials_are_redacted_before_the_email_rule() -> None:
    result = redact_for_remote("conecta a https://user:pa55word@example.com/repo y avisa a a@b.co")

    assert result.text == (
        "conecta a https://[REDACTED_CREDENTIAL]@example.com/repo y avisa a [REDACTED_EMAIL]"
    )
    assert result.categories == frozenset({"url_credentials", "email"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Escríbele a mi esposa Ana María López que llego tarde",
            "Escríbele a mi esposa [REDACTED_NAME] que llego tarde",
        ),
        ("Recuérdame llamar a mi amigo Carlos", "Recuérdame llamar a mi amigo [REDACTED_NAME]"),
        ("Agenda cita con la Dra. Pérez", "Agenda cita con la Dra. [REDACTED_NAME]"),
        ("my name is John Smith", "my name is [REDACTED_NAME]"),
        ("Saluda a Doña Rosa", "Saluda a Doña [REDACTED_NAME]"),
    ],
)
def test_cue_introduced_names_are_masked(text: str, expected: str) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({"person_name"})


def test_recognized_names_need_a_personal_context() -> None:
    public = redact_for_remote("¿Quién es Lionel Messi?", person_names=("Lionel Messi",))
    private = redact_for_remote(
        "Avísale a mi colega Laura Pérez y a Laura", person_names=("Laura", "Laura Pérez")
    )

    assert public.text == "¿Quién es Lionel Messi?"
    assert private.text == "Avísale a mi colega [REDACTED_NAME] y a [REDACTED_NAME]"
    assert private.categories == frozenset({"person_name"})


def test_assistant_and_vendor_names_are_never_masked() -> None:
    result = redact_for_remote("mi asistente Jarvis", person_names=("Jarvis",))

    assert result.text == "mi asistente Jarvis"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vivo en Av. Amazonas N24-15 y Colón, Quito", "Vivo en [REDACTED_ADDRESS], Quito"),
        ("Mi casa: Cdla. Kennedy Norte Mz 5 Villa 12", "Mi casa: [REDACTED_ADDRESS]"),
        ("Calle Rocafuerte #305, Guayaquil", "[REDACTED_ADDRESS], Guayaquil"),
        (
            "Ship to 1600 Pennsylvania Avenue, Washington",
            "Ship to [REDACTED_ADDRESS], Washington",
        ),
    ],
)
def test_street_addresses_with_a_number_are_masked(text: str, expected: str) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({"postal_address"})


def test_street_names_without_a_house_number_are_kept() -> None:
    text = "¿Cómo llego a la Avenida 9 de Octubre desde el Malecón?"

    assert redact_for_remote(text).text == text


@pytest.mark.parametrize(
    ("text", "categories"),
    [
        ("Me diagnosticaron diabetes, ¿qué como?", {"health"}),
        ("tengo ansiedad desde hace semanas", {"health"}),
        ("mi sueldo no alcanza, ¿cómo ahorro?", {"personal_finance"}),
        ("soy gay y quiero contárselo a mis padres", {"sexual_orientation"}),
        ("me arrestaron hace años, ¿afecta mi visa?", {"criminal_record"}),
        ("¿qué síntomas tiene la gripe?", set()),
        ("¿cuál es el precio del dólar hoy?", set()),
        ("its value is std::vector", set()),
    ],
)
def test_sensitive_topics_require_a_first_person_subject(
    text: str, categories: set[str]
) -> None:
    assert sensitive_topics(text) == frozenset(categories)
