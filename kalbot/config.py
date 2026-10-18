"""Settings: built-in defaults merged with the user's config.yaml, plus .env keys.

The defaults live in kalbot/defaults.yaml. The user's config.yaml (gitignored)
holds only what they changed, so pulling a new version never conflicts with it.
"""

from __future__ import annotations

import copy
import datetime as dt
import logging
import os
import re
from pathlib import Path

import yaml

log = logging.getLogger("kalbot")

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_FILE = PACKAGE_DIR / "defaults.yaml"
# maps whose keys are free-form (no "unknown setting" warnings below them)
OPEN_MAPS = {"llm.models", "llm.fallback_models", "llm.api_key_env", "llm.effort", "texts", "patterns"}


class ConfigError(Exception):
    pass


def workspace() -> Path:
    """Folder that holds config.yaml, .env, the browser profile and run output.

    KALBOT_HOME wins; a source checkout (pip install -e .) uses its own folder;
    a regular install uses ~/.kalbot.
    """
    env = os.environ.get("KALBOT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    repo = PACKAGE_DIR.parent
    if (repo / "pyproject.toml").exists():
        return repo
    return Path.home() / ".kalbot"


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def unknown_keys(defaults: dict, user: dict, prefix: str = "") -> list[str]:
    """Dotted keys in the user's config that don't exist in the defaults (typos)."""
    out = []
    for k, v in (user or {}).items():
        key = f"{prefix}{k}"
        if k not in defaults:
            out.append(key)
        elif isinstance(v, dict) and isinstance(defaults[k], dict) and key not in OPEN_MAPS:
            out += unknown_keys(defaults[k], v, key + ".")
    return out


def windows_user_env(name: str) -> str:
    """A variable set with `setx` is only visible to terminals opened afterwards: read it from the registry."""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def get_env(name: str, default: str = "") -> str:
    """Read an environment variable from os.environ, falling back to the Windows user registry."""
    val = os.environ.get(name)
    if val is not None and val != "":
        return val.strip()
    win_val = windows_user_env(name)
    if win_val:
        return win_val.strip()
    return default


def load_dotenv(path: Path) -> list[str]:
    """Read KEY=VALUE lines into os.environ (real environment variables win)."""
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def load_config(path: str | Path | None = None) -> dict:
    """Defaults + the user's config.yaml. Adds cfg['_meta'] with paths and warnings."""
    home = workspace()
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))
    user_path = Path(path) if path else home / "config.yaml"
    user: dict = {}
    if user_path.exists():
        try:
            user = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{user_path} is not valid YAML:\n{e}") from None
        if not isinstance(user, dict):
            raise ConfigError(f"{user_path} must be a mapping of settings (key: value)")
    elif path:
        raise ConfigError(f"Config file not found: {user_path}")
    cfg = deep_merge(defaults, user)
    base = user_path.parent if path else home
    env_keys = load_dotenv(base / ".env")
    cfg["_meta"] = {
        "home": base,
        "config_path": user_path,
        "warnings": [f"Unknown setting '{k}' in {user_path.name} (typo?)" for k in unknown_keys(defaults, user)],
        "dotenv": env_keys,
    }
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    # Portal validation
    base_url = str(cfg.get("portal", {}).get("base_url", "")).strip()
    if not base_url or not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ConfigError(f"portal.base_url must be a valid http(s) URL, got {base_url!r}")
    sem = cfg.get("portal", {}).get("semester")
    if sem is not None:
        try:
            if int(sem) < 1:
                raise ConfigError("portal.semester must be a positive integer (>= 1)")
        except (ValueError, TypeError):
            raise ConfigError(f"portal.semester must be an integer, got {sem!r}") from None

    # LLM provider validation
    prov = str(cfg["llm"]["provider"]).strip().lower()
    if prov not in cfg["llm"]["models"]:
        raise ConfigError(f"llm.provider '{prov}' has no model in llm.models")

    # Delays validation
    d = cfg.get("delays", {})
    for key in ("action_min_s", "action_max_s"):
        if float(d[key]) < 0:
            raise ConfigError(f"delays.{key} must be >= 0")
    if float(d["action_min_s"]) > float(d["action_max_s"]):
        raise ConfigError(
            f"delays.action_min_s ({d['action_min_s']}) cannot be greater than "
            f"delays.action_max_s ({d['action_max_s']})"
        )

    # Timeouts validation
    for name, val in cfg.get("timeouts", {}).items():
        try:
            if float(val) < 0:
                raise ConfigError(f"timeouts.{name} must be >= 0")
        except (ValueError, TypeError):
            raise ConfigError(f"timeouts.{name} must be a number, got {val!r}") from None

    # Run options validation
    run_cfg = cfg.get("run", {})
    if "pass_fraction" in run_cfg:
        try:
            pf = float(run_cfg["pass_fraction"])
            if not 0.0 <= pf <= 1.0:
                raise ConfigError("run.pass_fraction must be between 0.0 and 1.0")
        except (ValueError, TypeError):
            raise ConfigError("run.pass_fraction must be a numeric value") from None
    if "limit" in run_cfg and run_cfg["limit"] is not None:
        try:
            if int(run_cfg["limit"]) < 0:
                raise ConfigError("run.limit must be >= 0")
        except (ValueError, TypeError):
            raise ConfigError("run.limit must be an integer") from None
    if "login_timeout_minutes" in run_cfg:
        try:
            if float(run_cfg["login_timeout_minutes"]) <= 0:
                raise ConfigError("run.login_timeout_minutes must be > 0")
        except (ValueError, TypeError):
            raise ConfigError("run.login_timeout_minutes must be a number") from None

    # Tasks validation
    tasks_cfg = cfg.get("tasks", {})
    for task_name, task_enabled in tasks_cfg.items():
        if not isinstance(task_enabled, bool):
            raise ConfigError(f"tasks.{task_name} must be a boolean (true/false), got {task_enabled!r}")

    # Browser validation
    b = cfg.get("browser", {})
    if "headless" in b and not isinstance(b["headless"], bool):
        raise ConfigError(f"browser.headless must be a boolean (true/false), got {b['headless']!r}")
    if "slow_mo_ms" in b and b["slow_mo_ms"] is not None:
        try:
            if float(b["slow_mo_ms"]) < 0:
                raise ConfigError("browser.slow_mo_ms must be >= 0")
        except (ValueError, TypeError):
            raise ConfigError("browser.slow_mo_ms must be a numeric value") from None

    # GitHub validation
    gh = cfg.get("github", {})
    if "api_url" in gh and gh["api_url"]:
        gh_url = str(gh["api_url"]).strip()
        if not (gh_url.startswith("http://") or gh_url.startswith("https://")):
            raise ConfigError(f"github.api_url must be a valid http(s) URL, got {gh_url!r}")
    if "auto_create" in gh and not isinstance(gh["auto_create"], bool):
        raise ConfigError(f"github.auto_create must be a boolean, got {gh['auto_create']!r}")

    # Links validation
    lk = cfg.get("links", {})
    if "wait_for_live_site_s" in lk and lk["wait_for_live_site_s"] is not None:
        try:
            if float(lk["wait_for_live_site_s"]) < 0:
                raise ConfigError("links.wait_for_live_site_s must be >= 0")
        except (ValueError, TypeError):
            raise ConfigError("links.wait_for_live_site_s must be a numeric value") from None

    # Coding validation
    cd = cfg.get("coding", {})
    if "max_fix_rounds" in cd and cd["max_fix_rounds"] is not None:
        try:
            if int(cd["max_fix_rounds"]) < 0:
                raise ConfigError("coding.max_fix_rounds must be >= 0")
        except (ValueError, TypeError):
            raise ConfigError("coding.max_fix_rounds must be an integer") from None

    # Regex patterns validation
    for group in ("patterns",):
        for name, pats in cfg[group].items():
            for p in pats:
                try:
                    re.compile(str(p))
                except re.error as e:
                    raise ConfigError(f"{group}.{name}: bad regex {p!r}: {e}") from None


def workspace_path(cfg: dict, target: str | Path) -> Path:
    """Resolve an arbitrary file or folder path against the active workspace."""
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = cfg["_meta"]["home"] / p
    return p


def path(cfg: dict, key: str, create: bool = True) -> Path:
    """Resolve one of cfg['paths'] against the workspace and create the folder."""
    p = Path(cfg["paths"][key]).expanduser()
    if not p.is_absolute():
        p = cfg["_meta"]["home"] / p
    if create:
        (p.parent if p.suffix else p).mkdir(parents=True, exist_ok=True)
    return p


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def strs(values) -> list[str]:
    # str() everything: YAML turns bare Yes/No/On/Off into booleans
    return [str(x) for x in values or []]


def js_config(cfg: dict) -> dict:
    """The subset of the settings the in-page helpers need."""
    s, t, p = cfg["selectors"], cfg["texts"], cfg["patterns"]
    return {
        "start": strs(t["start"]),
        "next": strs(t["next"]),
        "submit": strs(t["submit"]),
        "confirm": strs(t["confirm"]),
        "retake": strs(t["retake"]),
        "prev": strs(t["previous"]),
        "taskSubmit": strs(t["task_submit"]),
        "run": strs(t["run"]),
        "proceed": strs(t["proceed"]),
        "save": strs(t["save"]),
        "preSubmit": strs(t["pre_submit"]),
        "optionSelector": s.get("quiz_option") or "",
        "questionSelector": s.get("quiz_question") or "",
        "taskFieldSelector": s.get("task_field") or "",
        "livebookCardSelector": s.get("livebook_card") or "",
        "livebookHrefRegex": s["livebook_href_regex"],
        "luRowSelector": s.get("lu_row") or "",
        "luNumberRegex": s["lu_number_regex"],
        "moduleToggleSelector": s.get("module_toggle") or "",
        "moduleRegex": s["module_regex"],
        "exclude": strs(p["quiz_exclude"]),
        "multi": strs(p["multi_select"]),
        "completed": strs(p["quiz_completed"]),
        "fail": strs(p["result_fail"]),
        "pass": strs(p["result_pass"]),
        "taskExclude": strs(p["task_exclude"]),
        "taskDone": strs(p["task_done"]),
        "taskSuccess": strs(p["task_success"]),
        "taskError": strs(p["task_error"]),
    }


def save_overrides(config_path: Path, section: str, values: dict[str, str]) -> list[str]:
    """Add `section.key: value` lines to the user's config.yaml for keys it doesn't set yet.

    Edits the text in place so the user's comments survive; returns the keys written.
    """
    text = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else ("# Your kalbot settings. Only what differs from kalbot/defaults.yaml.\n")
    )
    try:
        current = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    have = current.get(section) or {}
    new = {k: v for k, v in values.items() if v and not have.get(k)}
    if not new:
        return []
    lines = "".join(f"  {k}: '{str(v).replace(chr(39), chr(39) * 2)}'\n" for k, v in new.items())
    header = re.search(rf"^{re.escape(section)}:[ \t]*(#.*)?\n", text, re.M)
    if section not in current:
        updated = text.rstrip("\n") + f"\n\n{section}:\n{lines}"
    elif header:
        updated = text[: header.end()] + lines + text[header.end() :]
    else:
        return []
    # drop now-duplicated empty keys (`key: ""`) the user had in that section
    for k in new:
        updated = re.sub(rf"^[ \t]+{re.escape(k)}:[ \t]*(\"\"|'')[ \t]*\n", "", updated, flags=re.M)
    try:
        check = yaml.safe_load(updated)
        assert all(check[section][k] == str(v) for k, v in new.items())
    except Exception as e:  # never leave a broken config behind
        log.warning("not writing %s to %s: %s", list(new), config_path, e)
        return []
    config_path.write_text(updated, encoding="utf-8")
    return list(new)
