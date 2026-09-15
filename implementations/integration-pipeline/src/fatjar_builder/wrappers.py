from __future__ import annotations
from pathlib import Path
from typing import List

from .utils import ensure_executable, which_or_fail


class WrapperSelector:
    @staticmethod
    def mvnw_is_usable(repo_root: Path) -> bool:
        return (repo_root / "mvnw").exists() and (repo_root / ".mvn/wrapper/maven-wrapper.jar").exists()

    @staticmethod
    def gradlew_is_usable(repo_root: Path) -> bool:
        return (repo_root / "gradlew").exists() and (repo_root / "gradle/wrapper/gradle-wrapper.jar").exists()

    def pick_mvn_cmd(self, repo_root: Path, module_dir: Path) -> List[str]:
        if self.mvnw_is_usable(repo_root):
            mvnw = repo_root / "mvnw"
            ensure_executable(mvnw)
            return [str(mvnw)]
        if (module_dir / "mvnw").exists() and self.mvnw_is_usable(module_dir):
            mvnw = module_dir / "mvnw"
            ensure_executable(mvnw)
            return [str(mvnw)]
        which_or_fail("mvn")
        return ["mvn"]

    def pick_gradle_cmd(self, repo_root: Path, module_dir: Path) -> List[str]:
        if self.gradlew_is_usable(repo_root):
            gradlew = repo_root / "gradlew"
            ensure_executable(gradlew)
            return [str(gradlew)]
        if (module_dir / "gradlew").exists() and self.gradlew_is_usable(module_dir):
            gradlew = module_dir / "gradlew"
            ensure_executable(gradlew)
            return [str(gradlew)]
        which_or_fail("gradle")
        return ["gradle"]