package jacoco;

import model.CoverageSet;
import org.jacoco.core.analysis.*;
import org.jacoco.core.tools.ExecFileLoader;

import java.io.File;
import java.io.IOException;
import java.util.*;

/**
 * Produces a CoverageSet of covered units at source-line and counter granularity.
 * A method-level unit alone is not sufficient for filtering: a candidate may cover
 * previously uncovered lines in a method that the manual suite already touched.
 */
public final class CoverageAnalyzer {

    private final File classesDir;
    private final String targetClassName;

    public CoverageAnalyzer(File classesDir) {
        this(classesDir, "");
    }

    public CoverageAnalyzer(File classesDir, String targetClassName) {
        this.classesDir = Objects.requireNonNull(classesDir, "classesDir");
        this.targetClassName = targetClassName == null ? "" : targetClassName.trim();
    }

    public static final class AnalysisResult {
        private final Map<String, IClassCoverage> perClass;
        private final CoverageSet coverageSet;

        private AnalysisResult(Map<String, IClassCoverage> perClass, CoverageSet coverageSet) {
            this.perClass = perClass;
            this.coverageSet = coverageSet;
        }

        public Map<String, IClassCoverage> getPerClass() {
            return perClass;
        }

        public CoverageSet getCoverageSet() {
            return coverageSet;
        }
    }

    public static final class LineDelta {
        public final List<Integer> newlyCovered = new ArrayList<>();
        public final List<Integer> upgradedToFull = new ArrayList<>();

        public boolean isEmpty() {
            return newlyCovered.isEmpty() && upgradedToFull.isEmpty();
        }
    }

    /**
     * Computes line-level coverage added by candidateExec compared to baselineExec.
     *
     * Key:
     *  - newlyCovered: NOT_COVERED -> PARTLY/FULLY
     *  - upgradedToFull: PARTLY -> FULLY
     */
    public Map<String, LineDelta> newlyCoveredLines(
            File baselineExec,
            File candidateExec) throws IOException {
        return newlyCoveredLines(analyzeExec(baselineExec), analyzeExec(candidateExec));
    }

    public Map<String, LineDelta> newlyCoveredLines(
            AnalysisResult baseline,
            AnalysisResult candidate) {

        Map<String, IClassCoverage> base = baseline.getPerClass();
        Map<String, IClassCoverage> cand = candidate.getPerClass();

        Map<String, LineDelta> result = new LinkedHashMap<>();

        for (Map.Entry<String, IClassCoverage> e : cand.entrySet()) {
            String fqcn = e.getKey();
            IClassCoverage c = e.getValue();
            IClassCoverage b = base.get(fqcn);

            int first = c.getFirstLine();
            int last  = c.getLastLine();
            if (first == -1 || last == -1) continue;

            LineDelta delta = new LineDelta();

            for (int line = first; line <= last; line++) {
                int candStatus = c.getLine(line).getStatus();
                int baseStatus = (b == null)
                        ? ICounter.NOT_COVERED
                        : b.getLine(line).getStatus();

                boolean candCovered = isCovered(candStatus);
                boolean baseCovered = isCovered(baseStatus);

                // NOT_COVERED -> PARTLY/FULLY
                if (candCovered && !baseCovered) {
                    delta.newlyCovered.add(line);
                }

                // PARTLY -> FULLY
                if (baseStatus == ICounter.PARTLY_COVERED
                        && candStatus == ICounter.FULLY_COVERED) {
                    delta.upgradedToFull.add(line);
                }
            }

            if (!delta.isEmpty()) {
                result.put(fqcn, delta);
            }
        }

        return result;
    }

    private boolean isCovered(int status) {
        return status == ICounter.PARTLY_COVERED
                || status == ICounter.FULLY_COVERED;
    }

    private boolean isFullyCovered(int status) {
        return status == ICounter.FULLY_COVERED;
    }

    public jacoco.TestDelta testDeltaTotals(File baselineExec, File candidateExec, String testSelector) throws IOException {
        return testDeltaTotals(analyzeExec(baselineExec), analyzeExec(candidateExec), testSelector);
    }

    public jacoco.TestDelta testDeltaTotals(AnalysisResult baseline, AnalysisResult candidate, String testSelector) {
        java.util.List<jacoco.ClassDelta> perClass = perClassDelta(baseline, candidate);

        int lines = 0, methods = 0, branches = 0, instr = 0;
        for (jacoco.ClassDelta d : perClass) {
            lines += d.getAddedLines();
            methods += d.getAddedMethods();
            branches += d.getAddedBranches();
            instr += d.getAddedInstructions();
        }
        return new jacoco.TestDelta(testSelector, lines, methods, branches, instr);
    }

    public List<ClassDelta> perClassDelta(File baselineExec, File candidateExec) throws IOException {
        return perClassDelta(analyzeExec(baselineExec), analyzeExec(candidateExec));
    }

    public List<ClassDelta> perClassDelta(AnalysisResult baseline, AnalysisResult candidate) {
        Map<String, IClassCoverage> base = baseline.getPerClass();
        Map<String, IClassCoverage> cand = candidate.getPerClass();

        List<ClassDelta> deltas = new ArrayList<>();
        for (Map.Entry<String, IClassCoverage> e : cand.entrySet()) {
            String cls = e.getKey();
            IClassCoverage c = e.getValue();
            IClassCoverage b = base.get(cls);

            int addedLines = diffCovered(c.getLineCounter(), b == null ? null : b.getLineCounter());
            int addedInstr = diffCovered(c.getInstructionCounter(), b == null ? null : b.getInstructionCounter());
            int addedBranches = diffCovered(c.getBranchCounter(), b == null ? null : b.getBranchCounter());
            int addedMethods = diffCovered(c.getMethodCounter(), b == null ? null : b.getMethodCounter());

            if (addedLines != 0 || addedInstr != 0 || addedBranches != 0 || addedMethods != 0) {
                deltas.add(new ClassDelta(cls, addedLines, addedInstr, addedBranches, addedMethods));
            }
        }

        // Sort by added lines desc (you can change to instructions, branches, etc.)
        deltas.sort(Comparator.comparingInt(ClassDelta::getAddedLines).reversed());
        return deltas;
    }

    private int diffCovered(ICounter cand, ICounter base) {
        int b = (base == null) ? 0 : base.getCoveredCount();
        return cand.getCoveredCount() - b;
    }

    public AnalysisResult analyzeExec(File execFile) throws IOException {
        Objects.requireNonNull(execFile, "execFile");
        ExecFileLoader loader = new ExecFileLoader();
        loader.load(execFile);
        return analyzeFromLoader(loader);
    }

    public AnalysisResult analyzeMergedExecs(File... execFiles) throws IOException {
        Objects.requireNonNull(execFiles, "execFiles");
        if (execFiles.length == 0) {
            throw new IllegalArgumentException("execFiles is empty");
        }
        ExecFileLoader loader = new ExecFileLoader();
        for (File execFile : execFiles) {
            Objects.requireNonNull(execFile, "execFile");
            loader.load(execFile);
        }
        return analyzeFromLoader(loader);
    }

    /**
     * Materializes the union of multiple JaCoCo execution files.
     *
     * A final incremental-coverage artifact must retain the exact frozen manual
     * baseline. Re-running a nondeterministic manual suite and treating that new
     * execution as the baseline can lose probes and make added coverage appear
     * negative. JaCoCo's execution-data merge is monotonic for matching class ids.
     */
    public void mergeExecs(File destination, File... execFiles) throws IOException {
        Objects.requireNonNull(destination, "destination");
        Objects.requireNonNull(execFiles, "execFiles");
        if (execFiles.length == 0) {
            throw new IllegalArgumentException("execFiles is empty");
        }
        ExecFileLoader loader = new ExecFileLoader();
        for (File execFile : execFiles) {
            Objects.requireNonNull(execFile, "execFile");
            loader.load(execFile);
        }
        loader.save(destination, false);
    }

    private AnalysisResult analyzeFromLoader(ExecFileLoader loader) throws IOException {
        CoverageBuilder builder = new CoverageBuilder();
        Analyzer analyzer = new Analyzer(loader.getExecutionDataStore(), builder);
        analyzer.analyzeAll(classesDir);

        Collection<IClassCoverage> selectedClasses = selectClasses(builder.getClasses());
        Map<String, IClassCoverage> out = new HashMap<>();
        Set<String> units = new HashSet<>();
        for (IClassCoverage cc : selectedClasses) {
            out.put(cc.getName().replace('/', '.'), cc);

            String className = cc.getName(); // internal name: pkg/Foo
            for (IMethodCoverage mc : cc.getMethods()) {
                String methodId = className + "::" + mc.getName() + mc.getDesc();

                if (mc.getLineCounter().getCoveredCount() > 0) {
                    int firstLine = mc.getFirstLine();
                    int lastLine = mc.getLastLine();
                    for (int line = firstLine; line <= lastLine; line++) {
                        if (cc.getLine(line).getStatus() != ICounter.NOT_COVERED) {
                            units.add(methodId + "|LINE:" + line);
                        }
                    }
                }
                for (int covered = 1; covered <= mc.getBranchCounter().getCoveredCount(); covered++) {
                    units.add(methodId + "|BRANCH:" + covered);
                }
                for (int covered = 1; covered <= mc.getInstructionCounter().getCoveredCount(); covered++) {
                    units.add(methodId + "|INSTRUCTION:" + covered);
                }
                if (mc.getMethodCounter().getCoveredCount() > 0) {
                    units.add(methodId + "|METHOD");
                }
            }
        }
        return new AnalysisResult(out, new CoverageSet(units));
    }

    /** Select all binary classes compiled from the CUT's Java source file. */
    private Collection<IClassCoverage> selectClasses(Collection<IClassCoverage> classes) {
        if (targetClassName.isEmpty()) {
            return classes;
        }
        String targetInternalName = targetClassName.replace('.', '/');
        IClassCoverage target = classes.stream()
                .filter(candidate -> candidate.getName().equals(targetInternalName))
                .findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "CUT class is absent from the analyzed bytecode: " + targetClassName));
        String sourceFileName = target.getSourceFileName();
        if (sourceFileName == null || sourceFileName.isBlank()) {
            throw new IllegalStateException(
                    "CUT bytecode has no source-file identity: " + targetClassName);
        }
        String packageName = packageName(target.getName());
        List<IClassCoverage> selected = classes.stream()
                .filter(candidate -> packageName(candidate.getName()).equals(packageName))
                .filter(candidate -> sourceFileName.equals(candidate.getSourceFileName()))
                .toList();
        if (selected.isEmpty()) {
            throw new IllegalStateException(
                    "No bytecode matched the CUT source file: " + targetClassName);
        }
        return selected;
    }

    private String packageName(String internalClassName) {
        int separator = internalClassName.lastIndexOf('/');
        return separator < 0 ? "" : internalClassName.substring(0, separator);
    }

    public CoverageSet analyze(File execFile) throws IOException {
        return analyzeExec(execFile).getCoverageSet();
    }
}
