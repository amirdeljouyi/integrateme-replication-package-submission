package app;

import jacoco.CoverageAnalyzer;
import model.CoverageSet;
import runner.TestTimeouts;

import java.io.File;
import java.io.IOException;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.CodeSource;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

public final class CoverageFilterApp {

    private final CoverageAnalyzer coverageAnalyzer;
    private final ForkedJacocoRunner runner;

    public CoverageFilterApp(CoverageAnalyzer coverageAnalyzer,
                             ForkedJacocoRunner runner) {
        this.coverageAnalyzer = Objects.requireNonNull(coverageAnalyzer);
        this.runner = Objects.requireNonNull(runner);
    }

    /* =========================
     * Main entrypoint
     * ========================= */
    public static void main(String[] args) throws Exception {
        if (args.length < 9) {
            throw new IllegalArgumentException(
                    "Usage:\n" +
                            "  <mode: class|filter>\n" +
                            "  <classesDirRel> <workDirRel>\n" +
                            "  <manualTestClass> <agtTestClass>\n" +
                            "  <jacocoAgentRel> <sutClassesRel> <libsDir>\n" +
                            "  <testClassesDirRel>\n" +
                            "  [testTimeoutMs]"
            );
        }

        String mode = args[0];
        if (args.length >= 10) {
            System.setProperty(TestTimeouts.TIMEOUT_PROP, args[9]);
        }

        File jarDir = resolveJarDir(CoverageFilterApp.class);

        // Resolve all file arguments: if the path provided is absolute, use it as-is;
        // otherwise, resolve relative to the jar's directory. This prevents accidental
        // concatenation of the jar directory with absolute-looking paths that are
        // missing a leading slash (e.g., "Users/..." on Unix) which would otherwise
        // result in invalid paths like ".../find-unique/Users/...".
        File classesInput = resolveMaybeAbsolute(jarDir, args[1]);
        File workDir = resolveMaybeAbsolute(jarDir, args[2]);

        String manualTestClass = args[3];
        String agtTestClass = args[4];

        File jacocoFile = resolveMaybeAbsolute(jarDir, args[5]);
        File sutClassesFile = resolveMaybeAbsolute(jarDir, args[6]);
        File libsDir = resolveMaybeAbsolute(jarDir, args[7]);
        File testClassesFile = resolveMaybeAbsolute(jarDir, args[8]);

        // Unpack classes if provided as a fat JAR; otherwise, use the directory directly
        File classesDir;
        File tempExtractDir = null;
        if (classesInput.isFile() && classesInput.getName().endsWith(".jar")) {
            java.nio.file.Path tmpDirPath = java.nio.file.Files.createTempDirectory("covfilter_classes_");
            tempExtractDir = tmpDirPath.toFile();
            java.util.jar.JarFile jarFile = new java.util.jar.JarFile(classesInput);
            try {
                java.util.Enumeration<java.util.jar.JarEntry> entries = jarFile.entries();
                while (entries.hasMoreElements()) {
                    java.util.jar.JarEntry entry = entries.nextElement();
                    // Skip multi-release classes and other nested versions to avoid
                    // duplicate class names (e.g., META-INF/versions/9/net/...)
                    String name = entry.getName();
                    if (entry.isDirectory()) {
                        continue;
                    }
                    // Skip multi-release versions, IDE-specific launcher resources, and
                    // '.class.ide-launcher-res' files that cause duplicate class names
                    if (name != null) {
                        if (name.startsWith("META-INF/versions/") || name.startsWith("META-INF/ide-deps/")) {
                            continue;
                        }
                        if (name.endsWith(".class.ide-launcher-res")) {
                            continue;
                        }
                    }
                    java.io.File outFile = new java.io.File(tempExtractDir, name);
                    java.io.File parent = outFile.getParentFile();
                    if (parent != null && !parent.exists()) {
                        parent.mkdirs();
                    }
                    try (java.io.InputStream in = jarFile.getInputStream(entry);
                         java.io.OutputStream out = new java.io.FileOutputStream(outFile)) {
                        in.transferTo(out);
                    }
                }
            } finally {
                jarFile.close();
            }
            classesDir = tempExtractDir.getCanonicalFile();
        } else {
            classesDir = classesInput.getCanonicalFile();
        }

        String jacocoAgentJar = jacocoFile.getCanonicalPath();
        String sutClassesPath = sutClassesFile.getCanonicalPath();
        String testClassesPath = testClassesFile.getCanonicalPath();
        String toolJarPath = resolveJarPath(CoverageFilterApp.class);

        ForkedJacocoRunner runner = new ForkedJacocoRunner(
                jacocoAgentJar,
                libsDir,
                sutClassesPath,
                testClassesPath,
                toolJarPath,
                "app.RunMany"
        );

        CoverageFilterApp app = new CoverageFilterApp(
                new CoverageAnalyzer(
                        classesDir,
                        System.getProperty("covfilter.targetFqcn", "")
                ),
                runner
        );

        app.run(mode, workDir.getCanonicalFile(), manualTestClass, agtTestClass);
    }

    public void runClassLevel(File workDir,
                              String manualTestClass,
                              String agtTestClass) throws Exception {

        workDir.mkdirs();

        File manualExec = new File(workDir, "manual.exec");
        File agtExec = new File(workDir, "agt.exec");

        runner.runTestClass(manualTestClass, manualExec, false);
        System.out.println("[CoverageFilterApp] wrote " + manualExec.getPath()
                + " size=" + manualExec.length());

        runner.runTestClass(agtTestClass, agtExec, false);
        System.out.println("[CoverageFilterApp] wrote " + agtExec.getPath()
                + " size=" + agtExec.length());

        CoverageSet manualCoverage = coverageAnalyzer.analyze(manualExec);
        CoverageSet agtCoverage = coverageAnalyzer.analyze(agtExec);

        System.out.println("Manual covered units: " +
                manualCoverage.getCoveredUnits().size());
        System.out.println("AGT covered units:    " +
                agtCoverage.getCoveredUnits().size());
        System.out.println("AGT-only coverage:    " +
                agtCoverage.subtract(manualCoverage).getCoveredUnits().size());
    }

    public void runIncrementalFiltering(File workDir,
                                        String manualTestClass,
                                        String agtTestClass) throws Exception {

        workDir.mkdirs();

        io.CsvReportWriter csvWriter = new io.CsvReportWriter();

        /* =========================
         * 1) Baseline: manual only
         * ========================= */
        File baselineExec = new File(workDir, "baseline_manual.exec");
        String canonicalBaseline = System.getProperty("covfilter.baselineExec", "").trim();
        boolean usesCanonicalBaseline = !canonicalBaseline.isEmpty();
        if (usesCanonicalBaseline) {
            java.nio.file.Files.copy(
                    new File(canonicalBaseline).toPath(),
                    baselineExec.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING
            );
            System.out.println("[CoverageFilterApp] using canonical baseline " + canonicalBaseline);
        } else {
            runner.runSelectors(java.util.List.of(manualTestClass), baselineExec, false);
        }

        jacoco.CoverageAnalyzer.AnalysisResult baselineAnalysis = coverageAnalyzer.analyzeExec(baselineExec);
        model.CoverageSet baseline = baselineAnalysis.getCoverageSet();

        /* =========================
         * 2) Discover AGT methods (forked)
         * ========================= */
        java.util.List<String> methods = runner.runAndCaptureLines(
                        "app.ListTests",
                        java.util.List.of(agtTestClass)
                ).stream()
                .filter(s -> s != null && s.startsWith(ListTests.METHOD_PREFIX))
                .map(s -> s.substring(ListTests.METHOD_PREFIX.length()))
                .filter(s -> !s.isBlank())
                // Parameterized engines may expose several invocation nodes
                // for one Java method. Incremental selection is method-level,
                // so evaluate that selector once rather than counting the same
                // source method multiple times.
                .distinct()
                .toList();

        System.out.println("[CoverageFilterApp] AGT methods discovered: " + methods.size());

        /* =========================
         * 3) Incremental filtering
         * ========================= */
        model.CoverageSet current = baseline;
        java.util.List<String> keptSelectors = new java.util.ArrayList<>();

        java.util.List<jacoco.TestDelta> allTestDeltas = new java.util.ArrayList<>();
        java.util.List<jacoco.TestDelta> keptTestDeltas = new java.util.ArrayList<>();

        java.util.List<model.LineDeltaRow> lineDeltaRows = new java.util.ArrayList<>();

        // Console spam control (CSV will still have full detail)
        final boolean PRINT_LINE_DELTAS_FOR_KEPT = false;

        for (int i = 0; i < methods.size(); i++) {
            String selector = agtTestClass + "#" + methods.get(i);
            File candExec = new File(workDir, "cand_" + i + ".exec");

            // Run candidate method only; baseline is merged during analysis.
            int candidateExit = runner.runSelectorsAllowingFailure(
                    java.util.List.of(selector), candExec, false
            );
            if (candidateExit != 0) {
                // A generated test can call System.exit or otherwise fail its fork. It contributes
                // no trustworthy coverage, but it must not abort filtering of the remaining tests.
                if (candExec.exists() && !candExec.delete()) {
                    candExec.deleteOnExit();
                }
                jacoco.TestDelta td = new jacoco.TestDelta(selector, 0, 0, 0, 0);
                allTestDeltas.add(td);
                System.out.println("[DROP] " + selector
                        + "  fork-exit=" + candidateExit
                        + " +lines=0 +methods=0 +branches=0 +instr=0");
                continue;
            }

            // Analyze baseline + candidate from merged exec data in one pass.
            jacoco.CoverageAnalyzer.AnalysisResult baselinePlusCandidate =
                    coverageAnalyzer.analyzeMergedExecs(baselineExec, candExec);

            // Candidate-only contribution beyond manual baseline.
            model.CoverageSet candAddedVsBaseline =
                    baselinePlusCandidate.getCoverageSet().subtract(baseline);

            // High-level totals for ranking (vs manual baseline).
            jacoco.TestDelta td = coverageAnalyzer.testDeltaTotals(baselineAnalysis, baselinePlusCandidate, selector);
            allTestDeltas.add(td);

            if (candAddedVsBaseline.addsAnythingBeyond(current)) {
                keptSelectors.add(selector);
                current = current.union(candAddedVsBaseline);

                keptTestDeltas.add(td);

                System.out.println("[KEEP] " + selector +
                        "  +lines=" + td.getAddedLines() +
                        " +methods=" + td.getAddedMethods() +
                        " +branches=" + td.getAddedBranches() +
                        " +instr=" + td.getAddedInstructions());

                // Per-test line attribution (store into CSV rows)
                java.util.Map<String, jacoco.CoverageAnalyzer.LineDelta> deltas =
                        coverageAnalyzer.newlyCoveredLines(baselineAnalysis, baselinePlusCandidate);

                for (java.util.Map.Entry<String, jacoco.CoverageAnalyzer.LineDelta> e : deltas.entrySet()) {
                    jacoco.CoverageAnalyzer.LineDelta d = e.getValue();
                    if (d == null || (d.newlyCovered.isEmpty() && d.upgradedToFull.isEmpty())) continue;

                    lineDeltaRows.add(new model.LineDeltaRow(
                            selector,
                            e.getKey(),
                            csvWriter.toRanges(d.newlyCovered),
                            csvWriter.toRanges(d.upgradedToFull)
                    ));
                }

                if (PRINT_LINE_DELTAS_FOR_KEPT) {
                    System.out.println("  (line deltas captured to CSV)");
                }

            } else {
                System.out.println("[DROP] " + selector +
                        "  +lines=" + td.getAddedLines() +
                        " +methods=" + td.getAddedMethods() +
                        " +branches=" + td.getAddedBranches() +
                        " +instr=" + td.getAddedInstructions());
            }
        }

        /* =========================
         * 4) Final aggregate run (manual + kept) + per-class ranking
         * ========================= */
        File finalExec = new File(workDir, "final_manual_plus_kept.exec");
        File retainedSuiteExec = new File(workDir, "retained_suite.exec");
        java.util.List<String> finalSelectors = new java.util.ArrayList<>();
        if (!usesCanonicalBaseline) {
            finalSelectors.add(manualTestClass);
        }
        finalSelectors.addAll(keptSelectors);

        // Execute the combined suite to verify interactions and failures, then merge
        // that evidence with the exact baseline execution. Replacing the baseline
        // with a second manual-suite run can lose probes in nondeterministic tests
        // and incorrectly cancel genuine retained-test coverage.
        if (finalSelectors.isEmpty()) {
            java.nio.file.Files.copy(
                    baselineExec.toPath(),
                    retainedSuiteExec.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING
            );
            java.nio.file.Files.copy(
                    baselineExec.toPath(),
                    finalExec.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING
            );
        } else {
            runner.runSelectors(finalSelectors, retainedSuiteExec, false);
            coverageAnalyzer.mergeExecs(finalExec, baselineExec, retainedSuiteExec);
        }

        java.util.List<jacoco.ClassDelta> classDeltas =
                coverageAnalyzer.perClassDelta(baselineExec, finalExec);

        /* =========================
         * 5) Sort rankings
         * ========================= */
        java.util.Comparator<jacoco.TestDelta> byImpact =
                java.util.Comparator
                        .comparingInt(jacoco.TestDelta::getAddedLines).reversed()
                        .thenComparingInt(jacoco.TestDelta::getAddedInstructions).reversed()
                        .thenComparingInt(jacoco.TestDelta::getAddedBranches).reversed()
                        .thenComparingInt(jacoco.TestDelta::getAddedMethods).reversed();

        allTestDeltas.sort(byImpact);
        keptTestDeltas.sort(byImpact);

        /* =========================
         * 6) Print brief summary
         * ========================= */
        System.out.println();
        System.out.println("Manual covered units: " + baseline.getCoveredUnits().size());
        System.out.println("Kept AGT methods:     " + keptSelectors.size());
        System.out.println("Final covered units:  " + current.getCoveredUnits().size());

        System.out.println("\nTop classes by added covered lines:");
        for (int i = 0; i < Math.min(15, classDeltas.size()); i++) {
            jacoco.ClassDelta d = classDeltas.get(i);
            System.out.printf(
                    "%2d) %s  +lines=%d +methods=%d +branches=%d +instr=%d%n",
                    i + 1,
                    d.getClassName(),
                    d.getAddedLines(),
                    d.getAddedMethods(),
                    d.getAddedBranches(),
                    d.getAddedInstructions()
            );
        }

        System.out.println("\nTop AGT test methods by added coverage (vs manual baseline) — ALL:");
        for (int i = 0; i < Math.min(10, allTestDeltas.size()); i++) {
            jacoco.TestDelta t = allTestDeltas.get(i);
            System.out.printf(
                    "%2d) %s  +lines=%d +methods=%d +branches=%d +instr=%d%n",
                    i + 1,
                    t.getTestSelector(),
                    t.getAddedLines(),
                    t.getAddedMethods(),
                    t.getAddedBranches(),
                    t.getAddedInstructions()
            );
        }

        System.out.println("\nTop AGT test methods by added coverage (vs manual baseline) — KEPT ONLY:");
        for (int i = 0; i < Math.min(10, keptTestDeltas.size()); i++) {
            jacoco.TestDelta t = keptTestDeltas.get(i);
            System.out.printf(
                    "%2d) %s  +lines=%d +methods=%d +branches=%d +instr=%d%n",
                    i + 1,
                    t.getTestSelector(),
                    t.getAddedLines(),
                    t.getAddedMethods(),
                    t.getAddedBranches(),
                    t.getAddedInstructions()
            );
        }

        /* =========================
         * 7) Write CSV reports
         * ========================= */
        csvWriter.writeKeptSelectors(new File(workDir, "kept_agt.csv"), keptSelectors);
        csvWriter.writeTestDeltas(new File(workDir, "test_deltas_all.csv"), allTestDeltas);
        csvWriter.writeTestDeltas(new File(workDir, "test_deltas_kept.csv"), keptTestDeltas);
        csvWriter.writeClassDeltas(new File(workDir, "class_deltas.csv"), classDeltas);
        csvWriter.writeLineDeltas(new File(workDir, "line_deltas_kept.csv"), lineDeltaRows);

        System.out.println("[CoverageFilterApp] CSVs written to: " + workDir.getPath());
    }

    /**
     * Prints line numbers in a compact way with a cap to avoid enormous logs.
     * Example: [12, 13, 14, ...] (truncated)
     */
    private void printLineList(java.util.List<Integer> lines, int max) {
        if (lines.size() <= max) {
            System.out.print(lines);
            return;
        }
        java.util.List<Integer> head = lines.subList(0, max);
        System.out.print(head);
        System.out.print("... (+" + (lines.size() - max) + ")");
    }

    private void writeLines(File file, List<String> lines) throws IOException {
        Files.createDirectories(file.toPath().getParent());
        Files.write(file.toPath(), lines, StandardCharsets.UTF_8);
    }

    /* =========================
     * Orchestration
     * ========================= */
    public void run(String mode, File workDir, String manualTestClass, String agtTestClass) throws Exception {

        if ("class".equalsIgnoreCase(mode)) {
            runClassLevel(workDir, manualTestClass, agtTestClass);
        } else if ("filter".equalsIgnoreCase(mode)) {
            runIncrementalFiltering(workDir, manualTestClass, agtTestClass);
        } else {
            throw new IllegalArgumentException("Unknown mode: " + mode);
        }
    }


    /* =========================
     * Jar directory resolver
     * ========================= */
    private static File resolveJarDir(Class<?> anchor) {
        File location = resolveJarFile(anchor);
        return location.isFile()
                ? Objects.requireNonNull(location.getParentFile())
                : location;
    }

    private static String resolveJarPath(Class<?> anchor) {
        return resolveJarFile(anchor).getAbsolutePath();
    }

    private static File resolveJarFile(Class<?> anchor) {
        try {
            CodeSource cs = anchor.getProtectionDomain().getCodeSource();
            if (cs == null || cs.getLocation() == null) {
                return new File(".").getAbsoluteFile();
            }
            return new File(cs.getLocation().toURI());
        } catch (URISyntaxException e) {
            return new File(".").getAbsoluteFile();
        }
    }

    /**
     * Resolve a path argument relative to the jar directory unless it's already absolute.
     * This helper avoids accidentally prefixing the jar directory onto absolute paths or
     * pseudo-absolute paths that lack a leading slash (e.g., "Users/..." on Unix).
     *
     * @param jarDir the base directory to resolve relative paths against
     * @param arg the raw argument string
     * @return a File representing the resolved path
     */
    private static File resolveMaybeAbsolute(File jarDir, String arg) {
        File f = new File(arg);
        // If the path starts with a file separator (Unix) or has a drive letter (Windows), treat it as absolute.
        if (f.isAbsolute()) {
            return f;
        }
        // Additionally, on Unix-like systems, a path starting with "Users/" is likely intended to be absolute
        // (macOS often begins absolute paths with "/Users/..."), so check for that case.
        String sep = File.separator;
        if (sep.equals("/") && arg.startsWith("Users" + sep)) {
            return new File(sep + arg);
        }
        return new File(jarDir, arg);
    }
}
