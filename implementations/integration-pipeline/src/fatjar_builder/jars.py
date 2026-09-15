from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
import zipfile

@dataclass
class JarPicker:
    fat_hints: List[str] = None

    def __post_init__(self):
        if self.fat_hints is None:
            self.fat_hints = ["jar-with-dependencies", "all", "uber", "with-dependencies", "shadow", "shaded"]

    @staticmethod
    def _is_bad_classifier(name: str) -> bool:
        n = name.lower()
        # common non-runtime jars
        bad_tokens = [
            "-sources", "-javadoc", "-plain",
            "-tests", "-test", "-it", "-integration-test",
        ]
        return any(t in n for t in bad_tokens)

    @staticmethod
    def _is_original_backup(name: str) -> bool:
        n = name.lower()
        return "original-" in n or "-original" in n

    def is_fat(self, jar: Path) -> bool:
        n = jar.name.lower()
        return any(h in n for h in self.fat_hints)

    def is_fat_archive(self, jar: Path) -> bool:
        try:
            with zipfile.ZipFile(jar) as zf:
                names = zf.namelist()
        except Exception:
            return False
        if self.is_fat(jar):
            # Name hints alone are not enough; require packaged classes or nested libs.
            if any(n.endswith(".class") for n in names):
                return True
        # Spring Boot executable jars package dependencies as nested jars.
        if any(n.startswith("BOOT-INF/lib/") and n.endswith(".jar") for n in names):
            return True
        if any(n.startswith("WEB-INF/lib/") and n.endswith(".jar") for n in names):
            return True
        return False

    @staticmethod
    def contains_class(jar: Path, class_entry: str) -> bool:
        try:
            with zipfile.ZipFile(jar) as zf:
                return class_entry in zf.namelist()
        except Exception:
            return False

    def pick_best_jar(self, jar_dir: Path) -> Optional[Path]:
        if not jar_dir.exists():
            return None

        jars = sorted(jar_dir.glob("*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
        jars = [j for j in jars if not self._is_bad_classifier(j.name)]
        if not jars:
            return None

        fat = [j for j in jars if self.is_fat_archive(j)]
        if fat:
            fat.sort(key=lambda p: (self._is_original_backup(p.name), -p.stat().st_mtime))
            return fat[0]
        jars.sort(key=lambda p: (self._is_original_backup(p.name), -p.stat().st_mtime))
        return jars[0]

    def pick_best_fat_jar(self, jar_dir: Path) -> Optional[Path]:
        if not jar_dir.exists():
            return None
        jars = sorted(jar_dir.glob("*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
        jars = [j for j in jars if not self._is_bad_classifier(j.name)]
        fat = [j for j in jars if self.is_fat_archive(j)]
        fat.sort(key=lambda p: (self._is_original_backup(p.name), -p.stat().st_mtime))
        return fat[0] if fat else None
