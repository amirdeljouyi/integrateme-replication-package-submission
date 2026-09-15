from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from .command_runner import CommandRunner
from .exceptions import CmdError, SkipBuild
from .wrappers import WrapperSelector
from .build_root import BuildRootDetector
from .jars import JarPicker
from .utils import set_java
from .pom_patcher import PomPatcher


@dataclass
class MavenFailure:
    code: str
    detail: str


def classify_maven_failure(out: str) -> MavenFailure:
    o = (out or "").lower()

    # reactor / module selection issues
    if "could not find the selected project in the reactor" in o:
        return MavenFailure("REACTOR_MISSING", "Module not in reactor (-pl mismatch or wrong root pom).")

    # common aggregator build / packaging
    if "packaging pom" in o or "is not a jar" in o:
        return MavenFailure("PACKAGING_NOT_JAR", "Ran on an aggregator/non-jar module.")

    # assembly specific
    if "no assembly descriptors found" in o:
        return MavenFailure(
            "ASSEMBLY_NO_DESCRIPTORS",
            "Assembly plugin ran without descriptor configuration (common when invoking default-cli on reactor/parent).",
        )

    if "skipassembly" in o and ("true" in o or "skipping" in o):
        return MavenFailure("ASSEMBLY_SKIPPED", "Assembly plugin appears skipped by property/profile.")

    # no main artifact / no jar produced
    if "the project has not been built yet" in o or "no file assigned to build artifact" in o:
        return MavenFailure("NO_MAIN_ARTIFACT", "Module did not produce a main artifact (no jar).")

    # compiler / java version constraints
    if "release version" in o and "not supported" in o:
        return MavenFailure("JDK_TOO_OLD", "Compiler release not supported by current JDK.")

    if "requires at least jdk" in o or "minimum java version" in o or "maven-enforcer-plugin" in o:
        return MavenFailure("ENFORCER_JDK", "Enforcer/toolchain requires a different JDK.")

    # dependency resolution
    if "could not resolve dependencies" in o or "could not find artifact" in o:
        return MavenFailure("DEPS_RESOLUTION", "Dependency resolution failed (missing artifact/repo/auth).")

    # fallback: show error-ish tail
    lines = [ln for ln in (out or "").splitlines() if "error" in ln.lower() or "failed" in ln.lower()]
    tail_lines = lines[-15:] if lines else (out or "").splitlines()[-20:]
    return MavenFailure("UNKNOWN", "Could not classify. Tail:\n" + "\n".join(tail_lines))


class MavenFatJarBuilder:
    """
    Strategy:
      1) package (reactor then standalone; with JDK21 retry if needed)
      2) if fat jar exists -> return
      3) try assembly CLI (descriptorRef=jar-with-dependencies) -> may fail on aggregator/parent in reactor
      4) if still missing -> patch *module pom* to bind assembly plugin to package -> package -> restore pom
      5) fallback shade
      6) return best jar (prefer non-test, prefer fat)
    """

    def __init__(
        self,
        runner: CommandRunner,
        wrappers: WrapperSelector,
        detector: BuildRootDetector,
        jar_picker: JarPicker,
        logger: Optional[logging.Logger] = None,
    ):
        self.runner = runner
        self.wrappers = wrappers
        self.detector = detector
        self.jar_picker = jar_picker
        self.logger = logger

    @staticmethod
    def maven_repo_extras(repo: str) -> List[str]:
        # Quarkus often needs this to avoid extension verification errors
        return ["-Dquarkus-extension-verify=false"] if repo == "quarkusio/quarkus" else []

    def _log_cmd_error(self, stage: str, err: CmdError) -> None:
        tail = (err.out or "")[-4000:]
        reason = classify_maven_failure(err.out)

        msg = f"{stage} failed: rc={err.rc} reason={reason.code} detail={reason.detail}"
        print(f"[WARN] {msg}")

        if self.logger:
            self.logger.warning("%s", msg)
            self.logger.warning("---- %s tail ----\n%s\n------------------", stage, tail)

    @staticmethod
    def _is_test_jar_name(name: str) -> bool:
        n = name.lower()
        return (
            n.endswith("-tests.jar")
            or n.endswith("-test.jar")
            or "test-fixtures" in n
            or "surefire" in n
            or "failsafe" in n
        )

    def _pick_best_non_test_jar(self, jar_dir: Path) -> Optional[Path]:
        """
        Extra safety: even if JarPicker is imperfect, avoid selecting *-tests.jar.
        """
        if not jar_dir.exists():
            return None

        jars = sorted(jar_dir.glob("*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
        jars = [
            j for j in jars
            if not any(x in j.name.lower() for x in ["sources", "javadoc", "-plain"])
        ]
        jars = [j for j in jars if not self._is_test_jar_name(j.name)]
        if not jars:
            return None

        jars.sort(
            key=lambda p: (
                self.jar_picker._is_original_backup(p.name),
                -p.stat().st_mtime,
            )
        )
        # prefer fat
        fat = [j for j in jars if self.jar_picker.is_fat(j)]
        return fat[0] if fat else jars[0]

    def build(self, repo: str, repo_root: Path, module_dir: Path, java21_home: str) -> Path:
        mvn = self.wrappers.pick_mvn_cmd(repo_root, module_dir)
        common = mvn + ["-q"]

        skips = [
            "-Dmaven.test.skip=true",
            "-DskipTests",
            "-DskipITs",
            "-Dinvoker.skip=true",
            "-Denforcer.skip=true",
            "-Dformatter.skip=true",
            "-Dlicense.skip=true",
            "-Dspotless.apply.skip=true",
            "-Dspotless.check.skip=true",
        ]
        extras = self.maven_repo_extras(repo)

        module_pom = self.detector.pick_module_pom(module_dir, repo_root)
        if not module_pom:
            raise FileNotFoundError(f"No pom.xml found for module_dir={module_dir}")

        root_pom = self.detector.pick_best_pom(repo_root, scope_dir=module_pom.parent) or module_pom
        reactor_dir = root_pom.parent
        rel = os.path.relpath(module_pom.parent, reactor_dir).replace("\\", "/")
        jar_dir = module_pom.parent / "target"

        base_env = os.environ.copy()
        use_reactor = (root_pom != module_pom)

        def attempt_reactor_package(env: dict) -> None:
            if root_pom == module_pom:
                raise CmdError(["mvn", "reactor-skip"], 1, "No distinct reactor pom; use standalone build")
            self.runner.run(
                common + skips + extras + ["-f", str(root_pom), "-am", "-pl", rel, "package"],
                cwd=reactor_dir,
                env=env,
            )

        def attempt_standalone_package(env: dict) -> None:
            self.runner.run(
                common + skips + extras + ["-f", str(module_pom), "package"],
                cwd=module_pom.parent,
                env=env,
            )

        def attempt_package(env: dict) -> None:
            if use_reactor:
                attempt_reactor_package(env)
            else:
                attempt_standalone_package(env)

        def attempt_assembly_cli(env: dict) -> None:
            # IMPORTANT: descriptorRef (not descriptorId)
            goal = "org.apache.maven.plugins:maven-assembly-plugin:3.6.0:single"
            args = [
                goal,
                "-DdescriptorRef=jar-with-dependencies",
                "-DappendAssemblyId=true",
                "-Dassembly.skipAssembly=false",
                "-DskipAssembly=false",
            ]
            if use_reactor:
                self.runner.run(
                    common + skips + extras + ["-f", str(root_pom), "-am", "-pl", rel] + args,
                    cwd=reactor_dir,
                    env=env,
                )
            else:
                self.runner.run(
                    common + skips + extras + ["-f", str(module_pom)] + args,
                    cwd=module_pom.parent,
                    env=env,
                )

        def attempt_shade(env: dict) -> None:
            shade_goal = "org.apache.maven.plugins:maven-shade-plugin:3.5.0:shade"
            shade_args = [shade_goal, "-DcreateDependencyReducedPom=false", "-DshadedArtifactAttached=false"]
            if use_reactor:
                self.runner.run(
                    common + skips + extras + ["-f", str(root_pom), "-am", "-pl", rel] + shade_args,
                    cwd=reactor_dir,
                    env=env,
                )
            else:
                self.runner.run(
                    common + skips + extras + ["-f", str(module_pom)] + shade_args,
                    cwd=module_pom.parent,
                    env=env,
                )

        # 1) package (reactor then standalone) + JDK21 retry
        try:
            attempt_reactor_package(base_env)
        except CmdError as e:
            if "reactor-skip" in " ".join(e.cmd) or "could not find the selected project in the reactor" in (e.out or "").lower():
                attempt_standalone_package(base_env)
            elif ("jdk 21" in (e.out or "").lower() or "requirejavaversion" in (e.out or "").lower()) and java21_home:
                env21 = set_java(base_env, java21_home)
                try:
                    attempt_reactor_package(env21)
                except CmdError as e2:
                    if "reactor-skip" in " ".join(e2.cmd) or "could not find the selected project in the reactor" in (e2.out or "").lower():
                        attempt_standalone_package(env21)
                    else:
                        raise
            else:
                raise

        # 2) if already fat => return (avoid *-tests.jar)
        jar = self._pick_best_non_test_jar(jar_dir) or self.jar_picker.pick_best_jar(jar_dir)
        if jar and self.jar_picker.is_fat_archive(jar):
            return jar

        # 3) Try assembly CLI (log failures)
        try:
            attempt_assembly_cli(base_env)
        except CmdError as e:
            self._log_cmd_error("maven-assembly CLI (jar-with-dependencies)", e)

        jar = self._pick_best_non_test_jar(jar_dir) or self.jar_picker.pick_best_jar(jar_dir)
        if jar and self.jar_picker.is_fat_archive(jar):
            return jar

        # 4) If still missing, patch *module pom* to bind assembly to package, then run package, then restore
        patcher = PomPatcher()
        patch_changed = False
        backup_path: Optional[Path] = None

        try:
            patch_result = patcher.ensure_assembly_plugin(module_pom)
            # support either object-return or tuple-return
            if hasattr(patch_result, "changed"):
                patch_changed = bool(getattr(patch_result, "changed"))
                backup_path = getattr(patch_result, "backup_path", None)
            elif isinstance(patch_result, tuple) and len(patch_result) >= 2:
                patch_changed = bool(patch_result[0])
                backup_path = patch_result[1]
            else:
                # unknown return; assume changed=false
                patch_changed = False

            if patch_changed:
                msg = f"Temporarily injected maven-assembly-plugin into module pom: {module_pom}"
                print(f"[INFO] {msg}")
                if self.logger:
                    self.logger.info(msg)

            try:
                attempt_package(base_env)
            except CmdError as e:
                self._log_cmd_error("package after pom patch", e)

        finally:
            if patch_changed:
                try:
                    # support restore(module_pom, backup_path) OR restore(module_pom)
                    if backup_path is not None:
                        patcher.restore(module_pom, backup_path)
                    else:
                        patcher.restore(module_pom)
                    msg = f"Restored original pom: {module_pom}"
                    if self.logger:
                        self.logger.info(msg)
                except Exception as restore_err:
                    msg = f"Failed to restore pom {module_pom}: {restore_err}"
                    print(f"[WARN] {msg}")
                    if self.logger:
                        self.logger.warning(msg)

        jar = self._pick_best_non_test_jar(jar_dir) or self.jar_picker.pick_best_jar(jar_dir)
        if jar and self.jar_picker.is_fat_archive(jar):
            return jar

        # 5) fallback shade (log failures too)
        try:
            attempt_shade(base_env)
        except CmdError as e:
            self._log_cmd_error("maven-shade", e)
            if ("jdk 21" in (e.out or "").lower() or "requirejavaversion" in (e.out or "").lower()) and java21_home:
                env21 = set_java(base_env, java21_home)
                attempt_shade(env21)

        # 6) final jar pick (avoid tests.jar)
        jar = self._pick_best_non_test_jar(jar_dir) or self.jar_picker.pick_best_jar(jar_dir)
        if not jar:
            raise FileNotFoundError(f"No jars produced in {jar_dir}")
        if not self.jar_picker.is_fat_archive(jar):
            raise SkipBuild(f"No fat Maven jar produced (selected plain jar: {jar.name})")
        return jar
