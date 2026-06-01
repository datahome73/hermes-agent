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
    assert "provider_for_key" not in script


def test_railway_start_preserves_deploy_env_over_persisted_dotenv() -> None:
    script = (ROOT / "docker" / "railway-start.sh").read_text(encoding="utf-8")

    assert "HERMES_PRESERVE_DEPLOY_ENV" in script


def test_railway_start_repairs_broken_config_from_template() -> None:
    script = (ROOT / "docker" / "railway-start.sh").read_text(encoding="utf-8")

    assert "cli-config.yaml.example" in script
    assert ".yaml.broken" in script
