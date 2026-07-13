import os
import sys


MODE_TO_SOURCE = {
    "mj": "majsoul",
    "th": "tenhou",
}
SOURCE_MODE_ALIASES = {
    "mj": "mj",
    "majsoul": "mj",
    "mahjong-soul": "mj",
    "0": "mj",
    "th": "th",
    "tenhou": "th",
    "1": "th",
}


def normalize_source_mode(value) -> str:
    """Normalize source selectors to the canonical config modes ``mj`` or ``th``."""
    if value is None:
        return "mj"

    key = str(value).strip().lower().replace("_", "-")
    mode = SOURCE_MODE_ALIASES.get(key)
    if not mode:
        raise ValueError(
            f"Unsupported source mode '{value}'. Use 'mj' for Mahjong Soul or 'th' for Tenhou."
        )
    return mode


def source_for_mode(mode) -> str:
    return MODE_TO_SOURCE[normalize_source_mode(mode)]


def resolve_mode_config(config: dict, requested_mode=None) -> tuple[str, str, dict]:
    """
    Select exactly one source-specific config section.

    New configs use ``mode: mj|th`` with ``mj:`` and ``th:`` mappings. The old
    top-level ``source: majsoul|tenhou`` format remains readable for backward
    compatibility. Defining both selectors is rejected so the active source is
    always unambiguous.
    """
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError("Config root must be a mapping/object.")
    configured_mode = config.get("mode")
    legacy_source = config.get("source")

    if configured_mode is not None and legacy_source is not None:
        raise ValueError(
            "Config defines both 'mode' and legacy 'source'. "
            "Keep exactly one selector: 'mode: mj' or 'mode: th'."
        )

    selector = requested_mode
    if selector is None:
        selector = configured_mode if configured_mode is not None else legacy_source
    mode = normalize_source_mode(selector)

    section = config.get(mode, {})
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError(f"Config section '{mode}' must be a mapping/object.")

    effective_config = dict(config)
    effective_config.update(section)
    if mode == "th" and "modes" not in section and "tenhou_modes" in config:
        effective_config["modes"] = config["tenhou_modes"]
    return mode, MODE_TO_SOURCE[mode], effective_config


def load_config(config_path: str = None) -> dict:
    """
    Load configuration from a YAML or TOML file.
    If config_path is not provided, looks for config.yaml or config.toml in the current directory.
    Returns a dictionary of the configuration.
    """
    if config_path is None:
        if os.path.exists("config.yaml"):
            config_path = "config.yaml"
        elif os.path.exists("config.yml"):
            config_path = "config.yml"
        elif os.path.exists("config.toml"):
            config_path = "config.toml"
        else:
            return {}

    if not os.path.exists(config_path):
        print(f"警告：找不到配置文件 '{config_path}'", file=sys.stderr)
        return {}

    ext = os.path.splitext(config_path)[1].lower()

    if ext in (".yaml", ".yml"):
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            print("错误：解析 YAML 配置文件需要安装 PyYAML。", file=sys.stderr)
            print("请运行: pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"解析 YAML 配置文件出错：{e}", file=sys.stderr)
            return {}

    elif ext == ".toml":
        try:
            if sys.version_info >= (3, 11):
                import tomllib
                with open(config_path, "rb") as f:
                    return tomllib.load(f)
            else:
                import tomli
                with open(config_path, "rb") as f:
                    return tomli.load(f)
        except ImportError:
            print("错误：解析 TOML 配置文件需要安装 tomli（对于 Python < 3.11）。", file=sys.stderr)
            print("请运行: pip install tomli", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"解析 TOML 配置文件出错：{e}", file=sys.stderr)
            return {}

    else:
        print(f"警告：不支持的配置文件格式 '{ext}'", file=sys.stderr)
        return {}
