"""
Characterization tests for TemplateLoader (src/generation/prompt_builder.py:526-610).

Real filesystem I/O against tmp_path fixture YAML files -- no network/model
dependency. Constructor eagerly validates templates_dir exists (raises
FileNotFoundError otherwise); precedence is explicit `templates_dir` arg >
RAGPIPE_PROMPT_TEMPLATES_DIR env var > default config/generation/prompts/.
Caching is a plain in-process dict with no TTL -- clear_cache() is the
only invalidation path.
"""

import pytest

from src.generation.prompt_builder import TemplateLoader

pytestmark = pytest.mark.characterization


def _write_template(dir_path, name, version=None, system=None, user=None):
    lines = []
    if version is not None:
        lines.append(f'version: "{version}"')
    if system is not None:
        lines.append(f'system: "{system}"')
    if user is not None:
        lines.append(f'user: "{user}"')
    (dir_path / f"{name}.yaml").write_text("\n".join(lines), encoding="utf-8")


class TestConstruction:
    def test_missing_templates_dir_raises_file_not_found(self, tmp_path):
        missing_dir = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError, match="Prompt templates directory not found"):
            TemplateLoader(templates_dir=str(missing_dir))

    def test_explicit_arg_takes_precedence_over_env_var(self, tmp_path, monkeypatch):
        explicit_dir = tmp_path / "explicit"
        explicit_dir.mkdir()
        env_dir = tmp_path / "env"
        env_dir.mkdir()
        monkeypatch.setenv("RAGPIPE_PROMPT_TEMPLATES_DIR", str(env_dir))

        loader = TemplateLoader(templates_dir=str(explicit_dir))
        assert loader.templates_dir == explicit_dir

    def test_env_var_used_when_no_explicit_arg(self, tmp_path, monkeypatch):
        env_dir = tmp_path / "env"
        env_dir.mkdir()
        monkeypatch.setenv("RAGPIPE_PROMPT_TEMPLATES_DIR", str(env_dir))

        loader = TemplateLoader()
        assert loader.templates_dir == env_dir

    def test_default_path_used_when_no_arg_and_no_env_var(self, monkeypatch):
        monkeypatch.delenv("RAGPIPE_PROMPT_TEMPLATES_DIR", raising=False)
        # The real default (config/generation/prompts/) exists in this repo.
        loader = TemplateLoader()
        assert loader.templates_dir.name == "prompts"
        assert loader.templates_dir.parent.name == "generation"


class TestLoad:
    def test_valid_template_with_all_fields(self, tmp_path):
        _write_template(
            tmp_path, "greeting", version="2.0.0", system="You are helpful.", user="Hello {name}"
        )
        loader = TemplateLoader(templates_dir=str(tmp_path))

        version, system, user = loader.load("greeting")
        assert version == "2.0.0"
        assert system == "You are helpful."
        assert user == "Hello {name}"

    def test_version_defaults_to_1_0_0_when_absent(self, tmp_path):
        _write_template(tmp_path, "no_version", system="sys", user="usr")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        version, _, _ = loader.load("no_version")
        assert version == "1.0.0"

    def test_only_system_field_is_valid(self, tmp_path):
        _write_template(tmp_path, "sys_only", system="Only system.")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        _, system, user = loader.load("sys_only")
        assert system == "Only system."
        assert user == ""

    def test_only_user_field_is_valid(self, tmp_path):
        _write_template(tmp_path, "user_only", user="Only user.")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        _, system, user = loader.load("user_only")
        assert system == ""
        assert user == "Only user."

    def test_both_fields_missing_raises_value_error(self, tmp_path):
        (tmp_path / "empty.yaml").write_text("other_field: x", encoding="utf-8")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        with pytest.raises(ValueError, match="missing both 'system' and 'user'"):
            loader.load("empty")

    def test_missing_template_file_raises_file_not_found_with_available_list(self, tmp_path):
        _write_template(tmp_path, "exists", system="s")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError, match="Available templates"):
            loader.load("does_not_exist")

    def test_empty_yaml_file_treated_as_missing_both_fields(self, tmp_path):
        (tmp_path / "blank.yaml").write_text("", encoding="utf-8")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        with pytest.raises(ValueError):
            loader.load("blank")


class TestCaching:
    def test_second_load_is_served_from_cache_not_re_read_from_disk(self, tmp_path):
        _write_template(tmp_path, "cached", system="original")
        loader = TemplateLoader(templates_dir=str(tmp_path))

        _, system1, _ = loader.load("cached")
        assert system1 == "original"

        # Mutate the file on disk after the first load.
        _write_template(tmp_path, "cached", system="changed")

        _, system2, _ = loader.load("cached")
        assert system2 == "original"  # stale cached value, not re-read

    def test_clear_cache_forces_a_re_read(self, tmp_path):
        _write_template(tmp_path, "cached", system="original")
        loader = TemplateLoader(templates_dir=str(tmp_path))

        loader.load("cached")
        _write_template(tmp_path, "cached", system="changed")
        loader.clear_cache()

        _, system, _ = loader.load("cached")
        assert system == "changed"


class TestGetVersion:
    def test_returns_only_the_version_string(self, tmp_path):
        _write_template(tmp_path, "versioned", version="3.1.4", system="s")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        assert loader.get_version("versioned") == "3.1.4"


class TestListAvailable:
    def test_lists_yaml_stems_in_the_templates_dir(self, tmp_path):
        _write_template(tmp_path, "one", system="s")
        _write_template(tmp_path, "two", system="s")
        loader = TemplateLoader(templates_dir=str(tmp_path))
        assert set(loader._list_available()) == {"one", "two"}
