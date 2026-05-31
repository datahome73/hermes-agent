from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_railway_start_uses_existing_container_entrypoint() -> None:
    script = (ROOT / "docker" / "railway-start.sh").read_text(encoding="utf-8")

    assert "exec hermes gateway run" in script
    assert "exec /init" not in script


def test_railway_start_maps_generic_api_key_to_provider_env() -> None:
    script = (ROOT / "docker" / "railway-start.sh").read_text(encoding="utf-8")

    assert "HERMES_API_KEY" in script
    assert "export DEEPSEEK_API_KEY=" in script
    assert "export OPENROUTER_API_KEY=" in script
