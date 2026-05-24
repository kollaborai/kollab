import kollabor_config.config_utils as config_utils


def test_initialize_system_prompt_finds_bundles_next_to_package(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    module_dir = site_packages / "kollabor_config"
    bundled_default = site_packages / "bundles" / "agents" / "default"
    bundled_default_sections = bundled_default / "sections"
    bundled_skill = site_packages / "bundles" / "skills" / "demo"

    module_dir.mkdir(parents=True)
    bundled_default_sections.mkdir(parents=True)
    bundled_skill.mkdir(parents=True)

    (bundled_default / "system_prompt.md").write_text(
        "You are the packaged default agent.", encoding="utf-8"
    )
    (bundled_default / "agent.json").write_text("{}", encoding="utf-8")
    (bundled_default_sections / "00-header.md").write_text(
        "Packaged section.", encoding="utf-8"
    )
    (bundled_skill / "SKILL.md").write_text("Packaged skill.", encoding="utf-8")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_utils,
        "__file__",
        str(module_dir / "config_utils.py"),
    )

    config_utils.initialize_system_prompt()

    seeded_default = home / ".kollab" / "agents" / "default"
    assert (seeded_default / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "You are the packaged default agent."
    assert (seeded_default / "agent.json").exists()
    assert (seeded_default / "sections" / "00-header.md").read_text(
        encoding="utf-8"
    ) == "Packaged section."
    assert (home / ".kollab" / "skills" / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "Packaged skill."


def test_create_agent_from_defaults_copies_nested_sections(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    module_dir = site_packages / "kollabor_config"
    bundled_agent = site_packages / "bundles" / "agents" / "default"
    bundled_sections = bundled_agent / "sections"

    module_dir.mkdir(parents=True)
    bundled_sections.mkdir(parents=True)
    (bundled_agent / "system_prompt.md").write_text("Prompt", encoding="utf-8")
    (bundled_sections / "00-header.md").write_text("Header", encoding="utf-8")

    monkeypatch.setattr(
        config_utils,
        "__file__",
        str(module_dir / "config_utils.py"),
    )

    target = tmp_path / "home" / ".kollab" / "agents" / "default"
    config_utils._create_agent_from_defaults(target)

    assert (target / "system_prompt.md").read_text(encoding="utf-8") == "Prompt"
    assert (target / "sections" / "00-header.md").read_text(
        encoding="utf-8"
    ) == "Header"


def test_get_bundled_skills_dir_finds_packaged_bundles(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    module_dir = site_packages / "kollabor_config"
    bundled_skill = site_packages / "bundles" / "skills" / "demo"

    module_dir.mkdir(parents=True)
    bundled_skill.mkdir(parents=True)
    (bundled_skill / "SKILL.md").write_text("Packaged skill.", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_utils,
        "__file__",
        str(module_dir / "config_utils.py"),
    )

    assert config_utils.get_bundled_skills_dir() == site_packages / "bundles" / "skills"


def test_initialize_user_directories_finds_packaged_bundles(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    module_dir = site_packages / "kollabor_config"
    bundled_themes = site_packages / "bundles" / "themes"
    bundled_layouts = site_packages / "bundles" / "layouts"
    bundled_widgets = site_packages / "bundles" / "widgets"

    module_dir.mkdir(parents=True)
    bundled_themes.mkdir(parents=True)
    bundled_layouts.mkdir(parents=True)
    bundled_widgets.mkdir(parents=True)

    (bundled_themes / "winter.json").write_text("{}", encoding="utf-8")
    (bundled_themes / "README.md").write_text("theme docs", encoding="utf-8")
    (bundled_layouts / "default.json").write_text("{}", encoding="utf-8")
    (bundled_layouts / "README.md").write_text("layout docs", encoding="utf-8")
    (bundled_widgets / "clock.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (bundled_widgets / "README.md").write_text("widget docs", encoding="utf-8")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_utils,
        "__file__",
        str(module_dir / "config_utils.py"),
    )

    config_utils.initialize_user_directories()

    assert (home / ".kollab" / "themes" / "winter.json").exists()
    assert (home / ".kollab" / "themes" / "README.md").read_text(
        encoding="utf-8"
    ) == "theme docs"
    assert (home / ".kollab" / "layouts" / "default.json").exists()
    assert (home / ".kollab" / "layouts" / "README.md").read_text(
        encoding="utf-8"
    ) == "layout docs"
    assert (home / ".kollab" / "status-widgets" / "clock.sh").exists()
    assert (home / ".kollab" / "status-widgets" / "README.md").read_text(
        encoding="utf-8"
    ) == "widget docs"
