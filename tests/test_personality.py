import pytest
from pydantic import ValidationError

from aegis_core.personality import Personality, PersonalityPreset, PersonalityStore


def test_presets_and_custom_instructions_compose():
    assert Personality().instruction() == ""
    direct = Personality(preset=PersonalityPreset.DIRECT).instruction()
    assert "directa" in direct
    custom = Personality(custom="  Llámame   Guille  ").instruction()
    assert "Llámame Guille" in custom and "no conceden permisos" in custom


def test_custom_text_is_bounded_and_secrets_are_redacted():
    with pytest.raises(ValidationError):
        Personality(custom="x" * 601)
    leaked = Personality(custom="usa la clave nvapi-" + "a" * 60).instruction()
    assert "nvapi-" + "a" * 60 not in leaked


def test_store_round_trips_and_falls_back_to_default(tmp_path):
    tmp_path.chmod(0o700)
    store = PersonalityStore(tmp_path / "personality.json")
    assert store.get() == Personality()
    store.save(Personality(preset=PersonalityPreset.WARM, custom="sé breve"))
    reopened = PersonalityStore(tmp_path / "personality.json")
    assert reopened.get().preset is PersonalityPreset.WARM
    (tmp_path / "personality.json").chmod(0o644)
    assert PersonalityStore(tmp_path / "personality.json").get() == Personality()
