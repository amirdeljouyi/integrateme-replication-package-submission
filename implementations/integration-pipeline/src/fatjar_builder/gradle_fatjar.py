from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from .command_runner import CommandRunner
from .exceptions import CmdError, SkipBuild
from .wrappers import WrapperSelector
from .build_root import BuildRootDetector
from .jars import JarPicker


class GradleFatJarBuilder:
    def __init__(self, runner: CommandRunner, wrappers: WrapperSelector, detector: BuildRootDetector, jar_picker: JarPicker):
        self.runner = runner
        self.wrappers = wrappers
        self.detector = detector
        self.jar_picker = jar_picker

    def build(self, cache_dir: Path, repo_root: Path, module_dir: Path) -> Path:
        env = os.environ.copy()
        env["GRADLE_USER_HOME"] = str(cache_dir / "gradle-home")

        gradle_root = self.detector.pick_gradle_root(repo_root, scope_dir=module_dir) or repo_root
        gradle = self.wrappers.pick_gradle_cmd(repo_root, gradle_root)
        project_path, project_dir = self._resolve_project(gradle, gradle_root, module_dir, env)

        libs_candidates = [project_dir / "build" / "libs", module_dir / "build" / "libs", gradle_root / "build" / "libs"]

        def pick_any_jar(require_fat: bool = True) -> Optional[Path]:
            for d in libs_candidates:
                j = self.jar_picker.pick_best_fat_jar(d) if require_fat else self.jar_picker.pick_best_jar(d)
                if j:
                    return j
            return None

        def qualify_task(task_name: str) -> str:
            return task_name if project_path == ":" else f"{project_path}:{task_name}"

        def run_gradle(args: List[str]) -> None:
            self.runner.run(
                gradle + ["--no-daemon", "-p", str(gradle_root)] + args,
                cwd=gradle_root,
                env=env,
            )

        def looks_like_jdk25(out: str) -> bool:
            return ("requires at least JDK 25" in out) or ("at least JDK 25" in out)

        def is_dep_verification(out: str) -> bool:
            return ("Dependency verification failed" in out) or ("verification-metadata.xml" in out)

        def is_missing_artifact(out: str) -> bool:
            return ("Could not find " in out) and ("Searched in the following locations" in out)

        def is_task_missing(out: str, task_name: str) -> bool:
            return (f"Task '{task_name}' not found" in out) or (f"Cannot locate tasks that match '{task_name}'" in out)

        def write_init_script_for_agt_fatjar() -> Path:
            script = """
import org.gradle.api.tasks.SourceSetContainer
import org.gradle.jvm.tasks.Jar

allprojects { p ->
  p.plugins.withId("java") {
    p.tasks.register("agtFatJar", Jar) {
      group = "build"
      description = "Builds an uber jar with runtime dependencies for AGT."
      archiveClassifier.set("agt-all")
      zip64 = true
      duplicatesStrategy = org.gradle.api.file.DuplicatesStrategy.EXCLUDE
      def sourceSets = p.extensions.getByType(SourceSetContainer)
      def mainSourceSet = sourceSets.named("main").get()
      def runtimeClasspath = p.configurations.getByName("runtimeClasspath")
      dependsOn(p.tasks.named("classes"))
      dependsOn(runtimeClasspath)
      from(mainSourceSet.output)
      from({
        runtimeClasspath
          .findAll { it.exists() }
          .collect { it.isDirectory() ? it : p.zipTree(it) }
      })
    }
  }
}
"""
            with tempfile.NamedTemporaryFile(prefix="agt-fatjar-", suffix=".gradle", mode="w", delete=False) as f:
                f.write(script)
                return Path(f.name)

        def run_with_common_retries(task_args: List[str]) -> None:
            try:
                run_gradle(task_args)
            except CmdError as e:
                if looks_like_jdk25(e.out):
                    raise SkipBuild("Requires at least JDK 25 (skipping)")
                if is_dep_verification(e.out):
                    run_gradle(["--dependency-verification=off"] + task_args)
                elif is_missing_artifact(e.out):
                    try:
                        run_gradle(["--refresh-dependencies"] + task_args)
                    except CmdError:
                        raise SkipBuild("Missing dependency artifact (cannot resolve)")
                else:
                    raise

        # Try known fat-jar tasks first when available.
        try:
            run_with_common_retries([qualify_task("shadowJar"), "-x", "test"])
        except CmdError as e:
            if not is_task_missing(e.out, qualify_task("shadowJar")) and not is_task_missing(e.out, "shadowJar"):
                raise
        jar = pick_any_jar(require_fat=True)
        if jar:
            return jar

        try:
            run_with_common_retries([qualify_task("bootJar"), "-x", "test"])
        except CmdError as e:
            if not is_task_missing(e.out, qualify_task("bootJar")) and not is_task_missing(e.out, "bootJar"):
                raise
        jar = pick_any_jar(require_fat=True)
        if jar:
            return jar

        # Deterministic fallback: inject our own fat-jar task and build it.
        init_script = write_init_script_for_agt_fatjar()
        try:
            run_with_common_retries(["-I", str(init_script), qualify_task("agtFatJar"), "-x", "test"])
        finally:
            init_script.unlink(missing_ok=True)

        jar = pick_any_jar(require_fat=True)
        if not jar:
            raise SkipBuild(
                "No fat Gradle jar produced (shadowJar/bootJar/agtFatJar unavailable)"
            )
        return jar

    def _resolve_project(self, gradle: List[str], gradle_root: Path, module_dir: Path, env: dict) -> tuple[str, Path]:
        init_script = self._write_project_locator_script()
        try:
            result = self.runner.run(
                gradle + ["--no-daemon", "-q", "-p", str(gradle_root), "-I", str(init_script), "agtListProjects"],
                cwd=gradle_root,
                env=env,
            )
        except CmdError:
            rel = os.path.relpath(module_dir, gradle_root).replace("\\", "/")
            if rel in ("", "."):
                return ":", gradle_root
            return ":" + rel.replace("/", ":"), module_dir
        finally:
            init_script.unlink(missing_ok=True)

        module_dir_resolved = module_dir.resolve()
        projects: List[tuple[str, Path]] = []
        for line in (result or "").splitlines():
            if not line.startswith("AGT_PROJECT|"):
                continue
            _, path, project_dir = line.split("|", 2)
            projects.append((path, Path(project_dir).resolve()))

        for path, project_dir in projects:
            if project_dir == module_dir_resolved:
                return path, project_dir

        for path, project_dir in sorted(projects, key=lambda item: len(str(item[1])), reverse=True):
            if module_dir_resolved == project_dir or module_dir_resolved.is_relative_to(project_dir):
                return path, project_dir

        rel = os.path.relpath(module_dir, gradle_root).replace("\\", "/")
        if rel in ("", "."):
            return ":", gradle_root
        return ":" + rel.replace("/", ":"), module_dir

    def _write_project_locator_script(self) -> Path:
        script = """
allprojects { p ->
  if (p == rootProject) {
    p.tasks.register("agtListProjects") {
      doLast {
        rootProject.allprojects.each { pr ->
          println("AGT_PROJECT|" + pr.path + "|" + pr.projectDir.canonicalPath)
        }
      }
    }
  }
}
"""
        with tempfile.NamedTemporaryFile(prefix="agt-projects-", suffix=".gradle", mode="w", delete=False) as f:
            f.write(script)
            return Path(f.name)
