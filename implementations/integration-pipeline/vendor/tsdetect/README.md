# tsDetect parser upgrade

`TestSmellDetector.jar` is tsDetect 2.2 rebuilt from upstream commit
`e89ce1a28d56db314d159e8957591aef6002173d` with:

- JavaParser `3.28.1` instead of `3.2.4`;
- `ParserConfiguration.LanguageLevel.BLEEDING_EDGE` for test and production
  source parsing;
- Kotlin Maven plugin `2.2.21`, required to build on JDK 25; and
- mechanical JavaParser API migrations in `Util`, `ConditionalTestLogic`,
  `EagerTest`, `LazyTest`, `IgnoredTest`, and `UnknownTest`.

No smell definition or threshold was changed. The compatibility migration in
`UnknownTest` deliberately preserves the upstream 2.2 behavior for annotations
with member-value pairs. On the 59 RQ1 artifacts parsed by the legacy jar, all
test-method and smell counts are identical. The upgraded jar parses all 83 RQ1
artifacts.

Build:

```sh
git clone https://github.com/TestSmells/TestSmellDetector.git
git -C TestSmellDetector checkout e89ce1a28d56db314d159e8957591aef6002173d
git -C TestSmellDetector apply --unidiff-zero /path/to/tsdetect-javaparser-3.28.1.patch
cd TestSmellDetector
mvn -Dmaven.test.skip=true clean package
```

The resulting assembly jar is
`target/TestSmellDetector-0.1-jar-with-dependencies.jar`.

SHA-256:

- upgraded `TestSmellDetector.jar`:
  `0ed98764dd19e20e1969e249f7a7203f459c045b5e367bdbae9fb70ed8dfa8c2`
- preserved `TestSmellDetector-legacy-2022.jar`:
  `6a12de6d1613c7ee9e845a2b69d876834f8ee98379e85d38c0e6027f7110f7bc`
