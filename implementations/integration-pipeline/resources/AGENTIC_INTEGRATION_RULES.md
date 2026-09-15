# AGENTS.md — Test Integration Rules (Manual + AGT)

## Goal
Integrate automatically generated tests (AGT) into the manually written test suite so the result matches the repo’s existing conventions:
- consistent coding style and naming
- consistent assertion style and messages
- reuse existing setup/fixtures/helpers
- normalized imports
- minimal, reviewable diffs
- no semantic drift

This repo is the source of truth for conventions. Prefer mimicking the closest existing manual tests in the same module/package.

> **Priority rule:** If it is possible to reuse manually written tests’ existing setup or helper logic, do it. Reuse repeated setup (`@Before/@BeforeEach`), existing fixtures/builders/factories, and existing helper/private methods instead of duplicating setup or logic in AGT tests.

---

## Hard Constraints (must follow)
1) **Do not change semantics**
   - Do not change the intended behavior tested by the AGT tests.
   - Do not weaken assertions (e.g., replacing specific checks with `assertTrue(x != null)`).
   - Do not remove meaningful assertions.
   - Merging/reordering/consolidating tests is allowed only as described in **Integration Rules → F) AGT/Manual Consolidation**, and only when behavior coverage and assertion strength are preserved.
   - Do not change production code.

2) **Do not refactor unrelated code**
   - Only touch files required to integrate the AGT tests.
   - Refactoring inside touched test files is allowed when needed for consolidation under **Integration Rules → F) AGT/Manual Consolidation**.
   - Avoid broad, repo-wide churn that is unrelated to the integration.

3) **Prefer reuse over duplication**
   - Always reuse existing setup (`@Before/@BeforeEach`, shared fixtures, builders, factories) when possible.
   - Always reuse existing helper methods/classes and utilities already used in manual tests when possible.
   - If an existing private helper in the same class matches the need, call it rather than re-implementing logic.
   - If setup is repeated across AGT tests, migrate it to the same setup pattern already used by nearby manual tests.

4) **No new helpers unless unavoidable**
   - Prefer existing helpers first.
   - You may introduce a small local helper when it materially improves readability or avoids duplication, including consolidation work under **Integration Rules → F) AGT/Manual Consolidation**.

5) **Keep tests deterministic**
   - Avoid randomness and time dependence. If unavoidable, seed randomness or use stable inputs.
   - Avoid flaky constructs (sleep, timing assumptions, environment dependencies).

6) **Keep tests readable**
   - Follow the manual suite’s structure (e.g., Arrange–Act–Assert if used).
   - Prefer meaningful variable names consistent with manual tests.

---

## Repo Convention Discovery (do this before editing)
Before making changes, identify and follow the repo’s conventions by inspecting nearby manual tests:
- Read the supplied repository guidance (such as `README`, `CONTRIBUTING.md`, and
  `AGENTS.md`) and follow its applicable test-writing and validation expectations.
- JUnit version and annotations (`org.junit.*` vs `org.junit.jupiter.*`)
- Assertion library (JUnit asserts, AssertJ, Hamcrest, Truth, etc.)
- Naming patterns (method names, test class naming)
- Setup patterns (shared fixture objects, base test classes, parameterized tests)
- Mocking patterns (Mockito/other) and initialization style
- Import ordering and static import conventions

If conventions differ by module, use the conventions of the target module.

---

## Integration Rules (what to do)
### A) Imports
- Remove unused imports.
- De-duplicate imports.
- Follow project import ordering rules (check existing tests or formatter rules).
- Use static imports only if the manual suite uses them for assertions.
- Prefer the same assertion imports used by manual tests in the same package/module.

### B) Test Setup / Fixtures
- If the manual suite uses shared setup, migrate AGT tests to reuse it.
- If there is a base test class or shared fixture builder, use it.
- Avoid re-initializing the same objects per test if the manual suite uses setup methods.
- If repeated test setup exists in AGT tests, extract/reuse it through existing `@Before/@BeforeEach` style setup used in manual tests.

### C) Helper/Private Method Reuse
- If manual tests already have helper methods that do the same task, call them.
- If an equivalent private helper already exists in the target test class, use it directly.
- If helper is `private` in another class and cannot be accessed, look for:
  - an equivalent public/protected helper
  - a shared `TestUtils`/builder
  - a base class method
- Avoid copy/pasting helper logic.

### D) Assertions
- Convert AGT assertions to the assertion style used in manual tests:
  - same library
  - same message conventions (if used)
  - same floating-point tolerances (if applicable)
- Preserve or improve specificity:
  - prefer `assertEquals(expected, actual)` over broad checks
  - keep exception assertions consistent with manual suite (`assertThrows` / `ExpectedException` / `try-catch` pattern)
- Make the test richer without changing its intended behavior: construct a realistic project-valid state, perform a meaningful operation, and assert observable outcomes strongly enough to detect a plausible regression. Do not add unrelated steps, invent requirements, or introduce complexity without testing value.
- Do not silently drop assertions.

### E) Naming and Structure
- Match manual test naming style (method names and class names).
- Keep tests logically grouped (nested classes/regions) if the suite uses them.
- Keep manually written tests and adopted AGT tests separated by explicit section comments (for example, `// Manual tests` and `// Adopted AGT tests`), unless the module already uses a different equivalent grouping style.
- Keep the “shape” of tests consistent:
  - Arrange
  - Act
  - Assert

### F) AGT/Manual Consolidation (Allowed)
- You may merge/consolidate AGT tests with existing manual tests when they validate the same rule or behavior.
- If a manual test already covers the same rule, fold AGT coverage into that manual test (or its group) instead of keeping duplicate test methods.
- You may rename/reorder merged tests to match local conventions.
- You may replace multiple overlapping tests with one clearer test (including parameterized form) when behavior coverage and assertion strength are preserved.
- Keep the merged result readable and aligned with local test structure.

---

## Output Requirements
When you finish, provide:
1) **A patch/diff** (preferred) or the changed files’ final contents.
2) A short summary listing:
   - which conventions were followed (JUnit/assertion library/setup style)
   - which helpers/fixtures were reused (file/class names)
   - any unavoidable deviations and why

Keep the summary factual and brief.

---

## Validation (must attempt)
Run the repo’s standard validation commands if available.
- Prefer the project’s documented commands (README/CONTRIBUTING).
- If none are documented, run the module’s tests using Maven/Gradle.

If tests fail:
- Fix only issues caused by your changes.
- Do not “fix” unrelated failures.

---

## Guardrails (never do)
- Do not add or modify production code.
- Do not add new external dependencies.
- Do not rewrite the whole test suite formatting.
- Do not introduce snapshot/regression golden files unless the repo already uses them.
- Do not add network calls or reliance on external services.

---

## If information is missing
If you cannot confidently infer a convention (e.g., assertion library or import style), pick the most common pattern in the closest manual tests in the same module/package and be consistent.
