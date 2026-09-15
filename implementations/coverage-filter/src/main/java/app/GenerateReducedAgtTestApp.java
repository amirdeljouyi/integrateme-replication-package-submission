package app;

import io.TestDeltaCsvReader;
import io.TopNReducedTestClassGenerator;
import jacoco.TestDelta;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

public final class GenerateReducedAgtTestApp {

    private final TestDeltaCsvReader reader;
    private final TopNReducedTestClassGenerator generator;

    public GenerateReducedAgtTestApp(TestDeltaCsvReader reader,
                                     TopNReducedTestClassGenerator generator) {
        this.reader = Objects.requireNonNull(reader, "reader");
        this.generator = Objects.requireNonNull(generator, "generator");
    }

    /**
     * Args:
     *  0: path to original AGT test source .java (e.g., .../QuteProcessor_1_ESTest.java)
     *  1: path to test_deltas CSV (e.g., .../test_deltas_kept.csv)
     *  2: N (e.g., 10)
     *  3: output dir for generated sources (e.g., .../generated-tests)
     *  4: sort (true|false)  -> if true, prioritizes target-class added lines (from line deltas, if available),
     *                           then sorts by added_lines, instr, branches, methods.
     */
    public void run(String[] args) throws Exception {
        if (args.length < 4) {
            throw new IllegalArgumentException(
                    "Usage: <originalTestJava> <testDeltasCsv> <N> <outDir> [sort=true|false]\n" +
                    "Example: .../QuteProcessor_1_ESTest.java .../test_deltas_kept.csv 10 tmp/generated-tests true"
            );
        }

        File originalTestJava = new File(args[0]);
        File testDeltasCsv = new File(args[1]);
        int n = Integer.parseInt(args[2]);
        File outDir = new File(args[3]);
        boolean sort = args.length >= 5 ? Boolean.parseBoolean(args[4]) : true;

        if (!originalTestJava.isFile()) {
            throw new IllegalArgumentException("originalTestJava not found: " + originalTestJava.getPath());
        }
        if (!testDeltasCsv.isFile()) {
            throw new IllegalArgumentException("testDeltasCsv not found: " + testDeltasCsv.getPath());
        }
        if (n <= 0) {
            throw new IllegalArgumentException("N must be > 0, got: " + n);
        }

        List<TestDelta> deltas = reader.read(testDeltasCsv);

        if (sort) {
            Map<String, Integer> targetAddedLines = loadTargetAddedLinesBySelector(testDeltasCsv, deltas);
            deltas.sort(Comparator
                    .comparingInt((TestDelta t) -> targetAddedLines.getOrDefault(t.getTestSelector(), 0))
                    .thenComparingInt(TestDelta::getAddedLines)
                    .thenComparingInt(TestDelta::getAddedInstructions)
                    .thenComparingInt(TestDelta::getAddedBranches)
                    .thenComparingInt(TestDelta::getAddedMethods)
                    .reversed());
        }

        generator.generateReducedClass(originalTestJava, deltas, n, outDir);

        System.out.println("[GenerateReducedAgtTestApp] Done. Output dir: " + outDir.getPath());
    }

    public static void main(String[] args) throws Exception {
        new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        ).run(args);
    }

    private Map<String, Integer> loadTargetAddedLinesBySelector(File testDeltasCsv,
                                                                List<TestDelta> deltas) {
        if (deltas == null || deltas.isEmpty()) {
            return Map.of();
        }

        String targetClassName = inferTargetClassName(deltas.get(0).getTestSelector());
        if (targetClassName == null || targetClassName.isBlank()) {
            return Map.of();
        }

        File lineDeltas = findLineDeltasCsv(testDeltasCsv);
        if (lineDeltas == null) {
            return Map.of();
        }

        Map<String, Set<Integer>> selectorToTargetLines = new HashMap<>();

        try (BufferedReader br = Files.newBufferedReader(lineDeltas.toPath(), StandardCharsets.UTF_8)) {
            String header = br.readLine(); // skip header
            if (header == null) {
                return Map.of();
            }

            String line;
            while ((line = br.readLine()) != null) {
                if (line.isBlank()) continue;

                String[] parts = line.split(",", -1);
                if (parts.length < 4) continue;

                String selector = unquote(parts[0].trim());
                String className = unquote(parts[1].trim());
                if (!isTargetOrInnerClass(targetClassName, className)) {
                    continue;
                }

                Set<Integer> covered = selectorToTargetLines.computeIfAbsent(selector, k -> new HashSet<>());
                addRanges(covered, unquote(parts[2].trim()));
                addRanges(covered, unquote(parts[3].trim()));
            }
        } catch (IOException ignored) {
            return Map.of();
        }

        if (selectorToTargetLines.isEmpty()) {
            return Map.of();
        }

        Map<String, Integer> out = new HashMap<>();
        for (Map.Entry<String, Set<Integer>> e : selectorToTargetLines.entrySet()) {
            out.put(e.getKey(), e.getValue().size());
        }
        return out;
    }

    private static File findLineDeltasCsv(File testDeltasCsv) {
        File parent = testDeltasCsv.getParentFile();
        if (parent == null) return null;
        File lineDeltas = new File(parent, "line_deltas_kept.csv");
        if (lineDeltas.isFile()) return lineDeltas;
        File linesDeltas = new File(parent, "lines_deltas_kept.csv");
        if (linesDeltas.isFile()) return linesDeltas;
        return null;
    }

    private static String inferTargetClassName(String testSelector) {
        if (testSelector == null || testSelector.isBlank()) return null;

        int hash = testSelector.indexOf('#');
        String testClassName = hash >= 0 ? testSelector.substring(0, hash) : testSelector;
        int lastDot = testClassName.lastIndexOf('.');

        String packageName = lastDot >= 0 ? testClassName.substring(0, lastDot) : "";
        String simpleName = lastDot >= 0 ? testClassName.substring(lastDot + 1) : testClassName;
        if (simpleName.isBlank()) return null;

        int eSTestIndex = simpleName.indexOf("_ESTest");
        String baseName = eSTestIndex > 0 ? simpleName.substring(0, eSTestIndex) : simpleName;
        if (baseName.endsWith("_Sanitized")) {
            baseName = baseName.substring(0, baseName.length() - "_Sanitized".length());
        }
        if (baseName.isBlank()) {
            return null;
        }

        return packageName.isEmpty() ? baseName : packageName + "." + baseName;
    }

    private static void addRanges(Set<Integer> target, String ranges) {
        if (ranges == null || ranges.isBlank()) return;

        String[] chunks = ranges.split(";");
        for (String chunk : chunks) {
            String value = chunk.trim();
            if (value.isEmpty()) continue;

            int dash = value.indexOf('-');
            if (dash < 0) {
                Integer line = parseIntSafe(value);
                if (line != null) {
                    target.add(line);
                }
                continue;
            }

            Integer start = parseIntSafe(value.substring(0, dash).trim());
            Integer end = parseIntSafe(value.substring(dash + 1).trim());
            if (start == null || end == null) continue;

            int from = Math.min(start, end);
            int to = Math.max(start, end);
            for (int i = from; i <= to; i++) {
                target.add(i);
            }
        }
    }

    private static Integer parseIntSafe(String value) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException ignored) {
            return null;
        }
    }

    private static String unquote(String value) {
        if (value == null) return "";
        if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
            return value.substring(1, value.length() - 1).replace("\"\"", "\"");
        }
        return value;
    }

    private static boolean isTargetOrInnerClass(String targetClassName, String className) {
        if (targetClassName == null || className == null) return false;
        return className.equals(targetClassName) || className.startsWith(targetClassName + "$");
    }
}
