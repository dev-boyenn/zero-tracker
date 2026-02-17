from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any


@dataclass
class MpkRuntimePaths:
    minecraft_dir: Path
    log_path: Path
    saves_dir: Path
    atum_json_path: Path
    atum_datapacks_dir: Path
    mods_dir: Path


@dataclass
class MpkInjectionToken:
    runtime: MpkRuntimePaths
    atum_json_backup_path: Path
    atum_jar_backup_path: Path
    atum_jar_target_path: Path
    datapack_dst_path: Path
    datapack_backup_path: Path
    had_existing_atum_jar: bool
    had_existing_datapack: bool
    prior_atum_jar_name: str | None
    disabled_ranked_mods: list[tuple[Path, Path]]
    fast_reset_config_path: Path | None
    fast_reset_backup_path: Path | None
    recipe_book_target_path: Path
    recipe_book_backup_path: Path
    had_existing_recipe_book_jar: bool


def _safe_unlink(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        try:
            os.chmod(path, 0o666)
            path.unlink()
        except Exception:
            pass


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except Exception:
        try:
            for child in path.rglob("*"):
                try:
                    os.chmod(child, 0o777)
                except Exception:
                    pass
            shutil.rmtree(path)
        except Exception:
            pass


class MpkInjector:
    DATAPACK_NAME = "zdash_tracker"
    DATAPACK_ENABLED_KEY = f"file/{DATAPACK_NAME}"
    RECIPE_BOOK_JAR_NAME = "recipe-book.jar"
    FAST_RESET_CANDIDATE_NAMES = (
        "fast_reset.json",
        "fast-reset.json",
        "fastreset.json",
        "fastReset.json",
    )

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.datapack_src = project_root / "datapack" / self.DATAPACK_NAME

    @staticmethod
    def _is_mcsr_ranked_mod(path: Path) -> bool:
        if not path.is_file():
            return False
        if path.suffix.lower() != ".jar":
            return False
        normalized = "".join(ch for ch in path.name.lower() if ch.isalnum())
        return "mcsr" in normalized and "ranked" in normalized

    def _disable_mcsr_ranked_mods(self, mods_dir: Path) -> tuple[list[tuple[Path, Path]] | None, str | None]:
        renamed: list[tuple[Path, Path]] = []
        try:
            candidates = sorted(
                [p for p in mods_dir.iterdir() if self._is_mcsr_ranked_mod(p)],
                key=lambda p: p.name.lower(),
            )
        except Exception as exc:
            return None, f"Failed to enumerate mods directory ({mods_dir}): {exc}"

        for src in candidates:
            dst = src.with_name(f"{src.name}.disabled")
            if dst.exists():
                for original, disabled in reversed(renamed):
                    try:
                        if disabled.exists():
                            disabled.rename(original)
                    except Exception:
                        pass
                return None, f"Cannot disable {src.name}: {dst.name} already exists."
            try:
                src.rename(dst)
                renamed.append((src, dst))
            except Exception as exc:
                for original, disabled in reversed(renamed):
                    try:
                        if disabled.exists():
                            disabled.rename(original)
                    except Exception:
                        pass
                return None, f"Failed to disable {src.name}: {exc}"
        return renamed, None

    def normalize_instance_path(self, raw_path: str | Path) -> tuple[Path | None, str | None]:
        raw_text = str(raw_path or "").strip().strip('"')
        if not raw_text:
            return None, "Path is empty."
        base = Path(raw_text).expanduser()
        if base.name.lower() == ".minecraft":
            minecraft_dir = base
        else:
            child = base / ".minecraft"
            minecraft_dir = child if child.exists() else base
        if not minecraft_dir.exists():
            return None, f"Path does not exist: {minecraft_dir}"
        if not minecraft_dir.is_dir():
            return None, f"Path is not a directory: {minecraft_dir}"
        return minecraft_dir.resolve(), None

    def runtime_from_minecraft_dir(self, minecraft_dir: Path) -> MpkRuntimePaths:
        return MpkRuntimePaths(
            minecraft_dir=minecraft_dir,
            log_path=minecraft_dir / "logs" / "latest.log",
            saves_dir=minecraft_dir / "saves",
            atum_json_path=minecraft_dir / "config" / "mcsr" / "atum.json",
            atum_datapacks_dir=minecraft_dir / "config" / "mcsr" / "atum" / "datapacks",
            mods_dir=minecraft_dir / "mods",
        )

    def _find_project_atum_jar(self) -> tuple[Path | None, str | None]:
        env_path = os.getenv("ZERO_DASH_ATUM_JAR_PATH", "").strip()
        if env_path:
            custom = Path(env_path).expanduser()
            if custom.exists() and custom.is_file():
                return custom.resolve(), None
            return None, f"ZERO_DASH_ATUM_JAR_PATH does not exist: {custom}"

        root_jars = [
            p
            for p in self.project_root.glob("atum*.jar")
            if p.is_file() and "sources" not in p.name.lower()
        ]
        if root_jars:
            root_jars.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return root_jars[0].resolve(), None

        libs_dir = self.project_root / "atum" / "build" / "libs"
        if not libs_dir.exists():
            return None, f"No repo-root Atum jar found and libs directory not found: {libs_dir}"
        jars = [
            p
            for p in libs_dir.glob("atum*.jar")
            if p.is_file() and "sources" not in p.name.lower()
        ]
        if not jars:
            return None, f"No Atum jar found in repo root or {libs_dir}"
        jars.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return jars[0].resolve(), None

    def _inject_atum_json(self, atum_json_path: Path, backup_path: Path) -> tuple[bool, str | None]:
        if not atum_json_path.exists():
            return False, f"Atum config not found: {atum_json_path}"
        atum_json_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(atum_json_path, backup_path)
        try:
            payload = json.loads(atum_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Failed parsing {atum_json_path}: {exc}"
        if not isinstance(payload, dict):
            return False, f"Invalid atum.json payload: {atum_json_path}"
        data_pack_settings = payload.get("dataPackSettings")
        if not isinstance(data_pack_settings, dict):
            data_pack_settings = {"enabled": ["vanilla"], "disabled": []}
            payload["dataPackSettings"] = data_pack_settings
        enabled = data_pack_settings.get("enabled")
        disabled = data_pack_settings.get("disabled")
        if not isinstance(enabled, list):
            enabled = []
            data_pack_settings["enabled"] = enabled
        if not isinstance(disabled, list):
            disabled = []
            data_pack_settings["disabled"] = disabled
        enabled_text = [str(v) for v in enabled]
        if self.DATAPACK_ENABLED_KEY not in enabled_text:
            enabled_text.append(self.DATAPACK_ENABLED_KEY)
        disabled_text = [str(v) for v in disabled if str(v) != self.DATAPACK_ENABLED_KEY]
        data_pack_settings["enabled"] = enabled_text
        data_pack_settings["disabled"] = disabled_text
        atum_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        return True, None

    def _find_fast_reset_config(self, minecraft_dir: Path) -> Path | None:
        config_dir = minecraft_dir / "config"
        if not config_dir.exists() or not config_dir.is_dir():
            return None

        candidates: list[Path] = []
        seen: set[str] = set()

        def _push(path: Path) -> None:
            key = str(path.resolve()).lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append(path)

        for name in self.FAST_RESET_CANDIDATE_NAMES:
            path = config_dir / name
            if path.exists() and path.is_file():
                _push(path)

        for path in config_dir.rglob("*.json"):
            low = path.name.lower()
            if "fast" in low and "reset" in low and path.is_file():
                _push(path)

        fallback: Path | None = candidates[0] if candidates else None
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict) and "alwaysSaveAfter" in payload:
                return path
        return fallback

    def _inject_fast_reset_config(self, config_path: Path, backup_path: Path) -> tuple[bool, str | None]:
        if not config_path.exists():
            return False, f"Fast reset config not found: {config_path}"
        shutil.copy2(config_path, backup_path)
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Failed parsing {config_path}: {exc}"
        if not isinstance(payload, dict):
            return False, f"Invalid fast reset payload: {config_path}"
        payload["alwaysSaveAfter"] = 5
        config_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        return True, None

    def apply(self, runtime: MpkRuntimePaths) -> tuple[MpkInjectionToken | None, str | None]:
        if not self.datapack_src.exists():
            return None, f"Datapack source not found: {self.datapack_src}"
        recipe_book_src = self.project_root / self.RECIPE_BOOK_JAR_NAME
        if not recipe_book_src.exists() or not recipe_book_src.is_file():
            return None, f"Recipe book mod not found: {recipe_book_src}"
        atum_jar, jar_error = self._find_project_atum_jar()
        if atum_jar is None:
            return None, jar_error or "Could not resolve Atum jar."

        runtime.atum_datapacks_dir.mkdir(parents=True, exist_ok=True)
        runtime.mods_dir.mkdir(parents=True, exist_ok=True)
        runtime.atum_json_path.parent.mkdir(parents=True, exist_ok=True)

        atum_json_backup = runtime.atum_json_path.parent / "atum_bak.json"
        legacy_atum_jar_backup = runtime.mods_dir / "atum_bak.jar"
        atum_jar_backup = runtime.mods_dir / "atum_bak.jar.disabled"
        recipe_book_target = runtime.mods_dir / self.RECIPE_BOOK_JAR_NAME
        recipe_book_backup = runtime.mods_dir / "recipe-book_bak.jar.disabled"
        datapack_dst = runtime.atum_datapacks_dir / self.DATAPACK_NAME
        datapack_backup = runtime.atum_datapacks_dir / f"{self.DATAPACK_NAME}_bak"

        # Keep the backup jar non-loadable by preserving the .disabled suffix.
        if legacy_atum_jar_backup.exists():
            if atum_jar_backup.exists():
                _safe_unlink(legacy_atum_jar_backup)
            else:
                legacy_atum_jar_backup.rename(atum_jar_backup)

        ok, json_error = self._inject_atum_json(runtime.atum_json_path, atum_json_backup)
        if not ok:
            return None, json_error or "Failed to update atum.json"

        fast_reset_config_path = self._find_fast_reset_config(runtime.minecraft_dir)
        fast_reset_backup_path: Path | None = None
        if fast_reset_config_path is not None:
            fast_reset_backup_path = fast_reset_config_path.with_name(f"{fast_reset_config_path.name}.zdash_bak")
            _safe_unlink(fast_reset_backup_path)
            ok, fast_reset_error = self._inject_fast_reset_config(fast_reset_config_path, fast_reset_backup_path)
            if not ok:
                return None, fast_reset_error or "Failed to update fast reset config"

        had_existing_datapack = datapack_dst.exists()
        _safe_rmtree(datapack_backup)
        if had_existing_datapack:
            shutil.move(str(datapack_dst), str(datapack_backup))
        shutil.copytree(self.datapack_src, datapack_dst)

        atum_jars = sorted(
            (
                p
                for p in runtime.mods_dir.glob("atum*.jar")
                if p.is_file() and p.name.lower() != atum_jar_backup.name.lower()
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        had_existing_atum_jar = len(atum_jars) > 0
        prior_atum_jar_name = atum_jars[0].name if atum_jars else None
        atum_jar_target = runtime.mods_dir / atum_jar.name
        if had_existing_atum_jar:
            shutil.copy2(atum_jars[0], atum_jar_backup)
        for old in atum_jars:
            _safe_unlink(old)
        shutil.copy2(atum_jar, atum_jar_target)

        had_existing_recipe_book_jar = recipe_book_target.exists()
        _safe_unlink(recipe_book_backup)
        if had_existing_recipe_book_jar:
            shutil.copy2(recipe_book_target, recipe_book_backup)
        shutil.copy2(recipe_book_src, recipe_book_target)

        token = MpkInjectionToken(
            runtime=runtime,
            atum_json_backup_path=atum_json_backup,
            atum_jar_backup_path=atum_jar_backup,
            atum_jar_target_path=atum_jar_target,
            datapack_dst_path=datapack_dst,
            datapack_backup_path=datapack_backup,
            had_existing_atum_jar=had_existing_atum_jar,
            had_existing_datapack=had_existing_datapack,
            prior_atum_jar_name=prior_atum_jar_name,
            disabled_ranked_mods=[],
            fast_reset_config_path=fast_reset_config_path,
            fast_reset_backup_path=fast_reset_backup_path,
            recipe_book_target_path=recipe_book_target,
            recipe_book_backup_path=recipe_book_backup,
            had_existing_recipe_book_jar=had_existing_recipe_book_jar,
        )
        disabled_ranked_mods, ranked_disable_error = self._disable_mcsr_ranked_mods(runtime.mods_dir)
        if ranked_disable_error is not None:
            rollback_error = self.revert(token)
            if rollback_error:
                return None, f"{ranked_disable_error}; rollback failed: {rollback_error}"
            return None, ranked_disable_error
        token.disabled_ranked_mods = disabled_ranked_mods or []
        return token, None

    def revert(self, token: MpkInjectionToken) -> str | None:
        errors: list[str] = []

        try:
            if token.runtime.atum_json_path.exists() and token.atum_json_backup_path.exists():
                shutil.copy2(token.atum_json_backup_path, token.runtime.atum_json_path)
            _safe_unlink(token.atum_json_backup_path)
        except Exception as exc:
            errors.append(f"atum.json restore failed: {exc}")

        try:
            if token.fast_reset_config_path is not None and token.fast_reset_backup_path is not None:
                if token.fast_reset_backup_path.exists():
                    shutil.copy2(token.fast_reset_backup_path, token.fast_reset_config_path)
                _safe_unlink(token.fast_reset_backup_path)
        except Exception as exc:
            errors.append(f"fast reset restore failed: {exc}")

        try:
            if token.had_existing_atum_jar and token.atum_jar_backup_path.exists():
                for old in token.runtime.mods_dir.glob("atum*.jar"):
                    if old.name.lower() == token.atum_jar_backup_path.name.lower():
                        continue
                    _safe_unlink(old)
                restore_name = token.prior_atum_jar_name or token.atum_jar_target_path.name
                restore_path = token.runtime.mods_dir / restore_name
                shutil.copy2(token.atum_jar_backup_path, restore_path)
            else:
                _safe_unlink(token.atum_jar_target_path)
            _safe_unlink(token.atum_jar_backup_path)
        except Exception as exc:
            errors.append(f"Atum jar restore failed: {exc}")

        try:
            if token.had_existing_recipe_book_jar and token.recipe_book_backup_path.exists():
                shutil.copy2(token.recipe_book_backup_path, token.recipe_book_target_path)
            else:
                _safe_unlink(token.recipe_book_target_path)
            _safe_unlink(token.recipe_book_backup_path)
        except Exception as exc:
            errors.append(f"recipe-book.jar restore failed: {exc}")

        try:
            if token.datapack_dst_path.exists():
                _safe_rmtree(token.datapack_dst_path)
            if token.had_existing_datapack and token.datapack_backup_path.exists():
                shutil.move(str(token.datapack_backup_path), str(token.datapack_dst_path))
            else:
                _safe_rmtree(token.datapack_backup_path)
        except Exception as exc:
            errors.append(f"Datapack restore failed: {exc}")

        try:
            for original_path, disabled_path in token.disabled_ranked_mods:
                if not disabled_path.exists():
                    continue
                if original_path.exists():
                    errors.append(
                        f"Ranked mod restore skipped for {disabled_path.name}: {original_path.name} already exists."
                    )
                    continue
                disabled_path.rename(original_path)
        except Exception as exc:
            errors.append(f"Ranked mod restore failed: {exc}")

        if not errors:
            return None
        return "; ".join(errors)

    def status_payload(
        self,
        *,
        runtime: MpkRuntimePaths | None,
        setup_required: bool,
        setup_error: str | None,
        injected: bool,
        configured_path: Path | None,
    ) -> dict[str, Any]:
        effective_runtime = runtime
        return {
            "mpk_setup_required": bool(setup_required),
            "mpk_setup_error": str(setup_error or ""),
            "mpk_injected": bool(injected),
            "mpk_instance_path": str(
                effective_runtime.minecraft_dir if effective_runtime is not None else (configured_path or "")
            ),
            "mpk_log_path": str(effective_runtime.log_path if effective_runtime is not None else ""),
            "mpk_saves_dir": str(effective_runtime.saves_dir if effective_runtime is not None else ""),
            "mpk_atum_json_path": str(
                effective_runtime.atum_json_path if effective_runtime is not None else ""
            ),
        }
