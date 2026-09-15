from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, TYPE_CHECKING

from ..core.java import (
    comment_compile_errors,
    expand_same_package_support_sources,
    index_candidate_java_files,
    prefer_repo_manual_sources,
    resolve_external_runtime_classpath_from_output,
)
from ..pipeline.helpers import adopted_variants
from .base import Step

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def _ordered_unique_paths(paths: Sequence[Path]) -> List[Path]:
    unique: List[Path] = []
    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _effective_libs_cp_for_sources(*, libs_glob_cp: str, java_files: Sequence[Path]) -> str:
    for java_file in java_files:
        try:
            text = java_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "org.evosuite.runtime" not in text:
            continue
        framework_cp = resolve_external_runtime_classpath_from_output(
            "\n".join(
                [
                    "ClassNotFoundException: org/evosuite/runtime/EvoRunner",
                    "ClassNotFoundException: org/evosuite/runtime/ViolatedAssumptionAnswer",
                    "ClassNotFoundException: org/evosuite/runtime/annotation/EvoSuiteClassExclude",
                    "ClassNotFoundException: org/evosuite/runtime/vnet/NonFunctionalRequirementRule",
                    "ClassNotFoundException: org/objectweb/asm/ClassVisitor",
                    "ClassNotFoundException: org/objectweb/asm/commons/Method",
                    "ClassNotFoundException: org/objectweb/asm/tree/ClassNode",
                    "ClassNotFoundException: org/objectweb/asm/tree/analysis/Analyzer",
                    "ClassNotFoundException: org/objectweb/asm/util/Printer",
                    "ClassNotFoundException: org/slf4j/LoggerFactory",
                ]
            )
        )
        if framework_cp:
            return ":".join(part for part in (libs_glob_cp, framework_cp) if part)
        break
    return libs_glob_cp


def _comment_source_sets(ctx: "TargetContext", adopted_src: Path) -> tuple[List[Path], List[Path]]:
    manual_sources = list(ctx.manual_sources)
    requested_sources = _ordered_unique_paths([*manual_sources, adopted_src])
    primary_sources = _ordered_unique_paths(
        expand_same_package_support_sources(ctx.repo_root_for_deps, requested_sources)
    )
    primary_additional = [source for source in primary_sources if source != adopted_src]

    if not manual_sources:
        return primary_additional, []

    repo_manual_sources = prefer_repo_manual_sources(ctx.repo_root_for_deps, manual_sources)
    if repo_manual_sources == manual_sources:
        return primary_additional, []

    fallback_sources = _ordered_unique_paths(
        expand_same_package_support_sources(
            ctx.repo_root_for_deps,
            [*repo_manual_sources, adopted_src],
        )
    )
    fallback_additional = [source for source in fallback_sources if source != adopted_src]
    if fallback_additional == primary_additional:
        return primary_additional, []
    return primary_additional, fallback_additional


class AdoptedCommentStep(Step):
    step_names = ("adopted-comment",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True

        variants = adopted_variants(self.pipeline.adopted_root, ctx.target_id, ctx.fqcn)
        if not variants:
            print(f'[agt] adopted-comment: Skip (missing adopted tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            return True

        repo_indexed_candidates: List[Path] = []
        if ctx.repo_root_for_deps and ctx.repo_root_for_deps.exists():
            repo_indexed_candidates = index_candidate_java_files(ctx.repo_root_for_deps)

        for variant, adopted_src in variants:
            if not adopted_src.exists():
                print(f'[agt] adopted-comment: Skip (missing {variant} source): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                continue
            comment_build = self.pipeline.build_dir / "adopted-comment-classes" / variant / ctx.target_id
            comment_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.comment.log"
            primary_additional_sources, fallback_additional_sources = _comment_source_sets(ctx, adopted_src)

            primary_candidate_java_files = _ordered_unique_paths(
                [adopted_src, *primary_additional_sources, *repo_indexed_candidates]
            )
            libs_cp = _effective_libs_cp_for_sources(
                libs_glob_cp=self.pipeline.args.libs_cp,
                java_files=[adopted_src, *primary_additional_sources],
            )
            print(f'[agt] Commenting compile errors ({variant}): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            ok = comment_compile_errors(
                test_file=adopted_src,
                build_dir=comment_build,
                log_file=comment_log,
                libs_glob_cp=libs_cp,
                sut_jar=ctx.sut_jar,
                additional_java_files=primary_additional_sources,
                candidate_java_files=primary_candidate_java_files,
            )
            if not ok and fallback_additional_sources:
                fallback_candidate_java_files = _ordered_unique_paths(
                    [adopted_src, *fallback_additional_sources, *repo_indexed_candidates]
                )
                fallback_libs_cp = _effective_libs_cp_for_sources(
                    libs_glob_cp=self.pipeline.args.libs_cp,
                    java_files=[adopted_src, *fallback_additional_sources],
                )
                ok = comment_compile_errors(
                    test_file=adopted_src,
                    build_dir=comment_build,
                    log_file=comment_log,
                    libs_glob_cp=fallback_libs_cp,
                    sut_jar=ctx.sut_jar,
                    additional_java_files=fallback_additional_sources,
                    candidate_java_files=fallback_candidate_java_files,
                )
                if ok:
                    print(
                        f'[agt] adopted-comment: repo-manual fallback succeeded: '
                        f'repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                    )
            if not ok:
                print(
                    f'[agt] adopted-comment: FAIL (see {comment_log}) repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                )
        return True
