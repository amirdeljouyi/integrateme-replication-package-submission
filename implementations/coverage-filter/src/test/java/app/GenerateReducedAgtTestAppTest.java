package app;

import io.TestDeltaCsvReader;
import io.TopNReducedTestClassGenerator;
import org.junit.Test;

import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class GenerateReducedAgtTestAppTest {
    @Test
    public void sortsDeltasDescendingBeforeSelectingTop1() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/quarkus-qute/QuteProcessor_ESTest_Adopted_Agentic.java");
        Path csvFile = resourcePath("fixtures/quarkus-qute/test_deltas_kept.csv");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "1",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("io")
                .resolve("quarkus")
                .resolve("qute")
                .resolve("deployment")
                .resolve("QuteProcessor_ESTest_Adopted_Agentic_Top1.java");

        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertTrue(generatedSource.contains("void processLoopElementHintWithNullExpressionThrowsNPE()"));
    }

    @Test
    public void sortsDeltasDescendingBeforeSelectingTop5() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/quarkus-qute/QuteProcessor_ESTest_Adopted_Agentic.java");
        Path csvFile = resourcePath("fixtures/quarkus-qute/test_deltas_kept.csv");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "5",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("io")
                .resolve("quarkus")
                .resolve("qute")
                .resolve("deployment")
                .resolve("QuteProcessor_ESTest_Adopted_Agentic_Top5.java");

        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertTrue(generatedSource.contains("void processLoopElementHintWithNullExpressionThrowsNPE()"));
        assertTrue(generatedSource.contains("void getNameOnInjectionPointThrowsIAEForInvalidType()"));
        assertTrue(generatedSource.contains("void initializeWithValidParamsButNonProxyRecorderThrowsIAE()"));
        assertTrue(generatedSource.contains("void extractMatchTypeReturnsNullWhenNoMatch()"));
        assertTrue(generatedSource.contains("void validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE()"));
        assertEquals(5, countOccurrences(generatedSource, "@Test"));
    }

    @Test
    public void preservesImportsFromOriginalClass() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/javaparser-clone/CloneVisitor_ESTest_Adopted_Agentic.java");
        Path csvFile = resourcePath("fixtures/javaparser-clone/test_deltas_kept.csv");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "5",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("com")
                .resolve("github")
                .resolve("javaparser")
                .resolve("ast")
                .resolve("visitor")
                .resolve("CloneVisitor_ESTest_Adopted_Agentic_Top5.java");

        String originalSource = Files.readString(testFile, StandardCharsets.UTF_8);
        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertEquals(extractImportBlock(originalSource), extractImportBlock(generatedSource));
    }

    @Test
    public void preservesClassFieldsAndMethodSignaturesAgainstExpectedOutput() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/javaparser-clone/CloneVisitor_ESTest_Adopted_Agentic.java");
        Path csvFile = resourcePath("fixtures/javaparser-clone/test_deltas_kept.csv");
        Path expectedFile = resourcePath("fixtures/javaparser-clone/CloneVisitor_ESTest_Adopted_Agentic_Top5.java");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "5",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("com")
                .resolve("github")
                .resolve("javaparser")
                .resolve("ast")
                .resolve("visitor")
                .resolve("CloneVisitor_ESTest_Adopted_Agentic_Top5.java");

        String expectedSource = Files.readString(expectedFile, StandardCharsets.UTF_8);
        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertEquals(extractClassName(expectedSource), extractClassName(generatedSource));
        assertEquals(extractFieldLines(expectedSource), extractFieldLines(generatedSource));
        assertEquals(extractMethodNames(expectedSource), extractMethodNames(generatedSource));
    }

    @Test
    public void preservesTestMethodBodiesAgainstExpectedOutput() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/javaparser-clone/CloneVisitor_ESTest_Adopted_Agentic.java");
        Path csvFile = resourcePath("fixtures/javaparser-clone/test_deltas_kept.csv");
        Path expectedFile = resourcePath("fixtures/javaparser-clone/CloneVisitor_ESTest_Adopted_Agentic_Top5.java");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "5",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("com")
                .resolve("github")
                .resolve("javaparser")
                .resolve("ast")
                .resolve("visitor")
                .resolve("CloneVisitor_ESTest_Adopted_Agentic_Top5.java");

        String expectedSource = Files.readString(expectedFile, StandardCharsets.UTF_8);
        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertEquals(extractTestMethods(expectedSource), extractTestMethods(generatedSource));
    }

    @Test
    public void preservesDruidFixturesAgainstExpectedOutput() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path testFile = resourcePath("fixtures/druid-datasource/DruidDataSource_ESTest_Adopted.java");
        Path csvFile = resourcePath("fixtures/druid-datasource/test_deltas_kept.csv");
        Path expectedFile = resourcePath("fixtures/druid-datasource/DruidDataSource_ESTest_Adopted_Top5.java");

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "5",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("com")
                .resolve("alibaba")
                .resolve("druid")
                .resolve("pool")
                .resolve("DruidDataSource_ESTest_Adopted_Top5.java");

        String expectedSource = Files.readString(expectedFile, StandardCharsets.UTF_8);
        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);

        assertEquals(extractImportBlock(expectedSource), extractImportBlock(generatedSource));
        assertEquals(extractClassName(expectedSource), extractClassName(generatedSource));
        assertEquals(extractFieldLines(expectedSource), extractFieldLines(generatedSource));
        assertEquals(extractMethodSignatures(expectedSource), extractMethodSignatures(generatedSource));
        assertEquals(extractTestMethods(expectedSource), extractTestMethods(generatedSource));
    }

    @Test
    public void prioritizesTargetClassCoverageWhenLineDeltasPresent() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path workDir = Files.createTempDirectory("agt-reduced-csv-");
        Path testFile = resourcePath("fixtures/quarkus-qute/QuteProcessor_ESTest_Adopted_Agentic.java");
        Path csvFile = workDir.resolve("test_deltas_kept.csv");
        Path lineDeltasFile = workDir.resolve("line_deltas_kept.csv");

        String selectorNoTarget = "io.quarkus.qute.deployment.QuteProcessor_ESTest_Adopted_Agentic#validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE";
        String selectorWithTarget = "io.quarkus.qute.deployment.QuteProcessor_ESTest_Adopted_Agentic#processLoopElementHintWithNullExpressionThrowsNPE";

        Files.writeString(
                csvFile,
                "test_selector,added_lines,added_methods,added_branches,added_instructions\n" +
                        selectorNoTarget + ",99,10,10,999\n" +
                        selectorWithTarget + ",1,1,1,1\n",
                StandardCharsets.UTF_8
        );

        Files.writeString(
                lineDeltasFile,
                "test_selector,class_name,newly_covered_lines,upgraded_to_full_lines\n" +
                        selectorNoTarget + ",io.quarkus.qute.ExpressionNode,20-30,\n" +
                        selectorWithTarget + ",io.quarkus.qute.deployment.QuteProcessor,101-103,\n",
                StandardCharsets.UTF_8
        );

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "1",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("io")
                .resolve("quarkus")
                .resolve("qute")
                .resolve("deployment")
                .resolve("QuteProcessor_ESTest_Adopted_Agentic_Top1.java");

        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);
        assertTrue(generatedSource.contains("void processLoopElementHintWithNullExpressionThrowsNPE()"));
        assertFalse(generatedSource.contains("void validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE()"));
    }

    @Test
    public void treatsTargetInnerClassCoverageAsTargetCoverage() throws Exception {
        Path outputDir = Files.createTempDirectory("agt-reduced-out-");
        Path workDir = Files.createTempDirectory("agt-reduced-csv-");
        Path testFile = resourcePath("fixtures/quarkus-qute/QuteProcessor_ESTest_Adopted_Agentic.java");
        Path csvFile = workDir.resolve("test_deltas_kept.csv");
        Path lineDeltasFile = workDir.resolve("line_deltas_kept.csv");

        String selectorNoTarget = "io.quarkus.qute.deployment.QuteProcessor_ESTest_Adopted_Agentic#validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE";
        String selectorWithTargetInner = "io.quarkus.qute.deployment.QuteProcessor_ESTest_Adopted_Agentic#processLoopElementHintWithNullExpressionThrowsNPE";

        Files.writeString(
                csvFile,
                "test_selector,added_lines,added_methods,added_branches,added_instructions\n" +
                        selectorNoTarget + ",99,10,10,999\n" +
                        selectorWithTargetInner + ",1,1,1,1\n",
                StandardCharsets.UTF_8
        );

        Files.writeString(
                lineDeltasFile,
                "test_selector,class_name,newly_covered_lines,upgraded_to_full_lines\n" +
                        selectorNoTarget + ",io.quarkus.qute.ExpressionNode,20-30,\n" +
                        selectorWithTargetInner + ",io.quarkus.qute.deployment.QuteProcessor$ViewSettings,101-103,\n",
                StandardCharsets.UTF_8
        );

        GenerateReducedAgtTestApp app = new GenerateReducedAgtTestApp(
                new TestDeltaCsvReader(),
                new TopNReducedTestClassGenerator()
        );

        app.run(new String[] {
                testFile.toString(),
                csvFile.toString(),
                "1",
                outputDir.toString(),
                "true"
        });

        Path generated = outputDir
                .resolve("io")
                .resolve("quarkus")
                .resolve("qute")
                .resolve("deployment")
                .resolve("QuteProcessor_ESTest_Adopted_Agentic_Top1.java");

        String generatedSource = Files.readString(generated, StandardCharsets.UTF_8);
        assertTrue(generatedSource.contains("void processLoopElementHintWithNullExpressionThrowsNPE()"));
        assertFalse(generatedSource.contains("void validateExpressionsWithNullBeanDeploymentCollectionsThrowsNPE()"));
    }

    private static Path resourcePath(String resource) throws Exception {
        URL url = GenerateReducedAgtTestAppTest.class.getClassLoader().getResource(resource);
        if (url == null) {
            throw new IllegalStateException("Missing test resource: " + resource);
        }
        return Path.of(url.toURI());
    }

    private static int countOccurrences(String text, String needle) {
        int count = 0;
        int idx = 0;
        while ((idx = text.indexOf(needle, idx)) != -1) {
            count++;
            idx += needle.length();
        }
        return count;
    }

    private static List<String> extractImportBlock(String source) {
        List<String> lines = source.lines().collect(java.util.stream.Collectors.toList());
        int start = -1;
        for (int i = 0; i < lines.size(); i++) {
            if (lines.get(i).startsWith("import ")) {
                start = i;
                break;
            }
        }
        if (start == -1) {
            return java.util.Collections.emptyList();
        }
        int end = start;
        for (int i = start + 1; i < lines.size(); i++) {
            String line = lines.get(i);
            if (line.startsWith("import ") || line.isBlank()) {
                end = i;
                continue;
            }
            break;
        }
        return lines.subList(start, end + 1);
    }

    private static String extractClassName(String source) {
        for (String line : source.lines().collect(java.util.stream.Collectors.toList())) {
            line = line.trim();
            if (line.startsWith("class ")) {
                String[] parts = line.split("\\s+");
                if (parts.length >= 2) {
                    return normalizeTopSuffix(parts[1]);
                }
            }
            if (line.startsWith("public class ")
                    || line.startsWith("protected class ")
                    || line.startsWith("private class ")) {
                String[] parts = line.split("\\s+");
                if (parts.length >= 3) {
                    return normalizeTopSuffix(parts[2]);
                }
            }
        }
        return "";
    }

    private static List<String> extractFieldLines(String source) {
        List<String> lines = source.lines().collect(java.util.stream.Collectors.toList());
        boolean inClass = false;
        List<String> fields = new java.util.ArrayList<>();
        for (String raw : lines) {
            String line = raw.trim();
            if (!inClass) {
                if (line.contains("class ") && line.endsWith("{")) {
                    inClass = true;
                }
                continue;
            }
            if (line.startsWith("@")) continue;
            if (line.contains("(") && line.contains(")") && line.endsWith("{")) {
                break;
            }
            if (line.endsWith(";") && !line.startsWith("import ") && !line.startsWith("package ")) {
                fields.add(line);
            }
        }
        return fields;
    }

    private static Set<String> extractMethodNames(String source) {
        Set<String> names = new TreeSet<>();
        for (String raw : source.lines().collect(java.util.stream.Collectors.toList())) {
            String line = raw.trim();
            if (!line.endsWith("{")) continue;
            if (line.startsWith("class ") || line.contains(" class ")) continue;
            int paren = line.indexOf('(');
            if (paren == -1) continue;
            String before = line.substring(0, paren).trim();
            String[] parts = before.split("\\s+");
            if (parts.length == 0) continue;
            names.add(parts[parts.length - 1]);
        }
        return names;
    }

    private static Set<String> extractMethodSignatures(String source) {
        Set<String> signatures = new TreeSet<>();
        for (String raw : source.lines().collect(java.util.stream.Collectors.toList())) {
            String line = raw.trim();
            if (!line.endsWith("{")) continue;
            if (!line.contains("(") || !line.contains(")")) continue;
            if (line.startsWith("class ") || line.contains(" class ")) continue;
            signatures.add(normalizeTopSuffix(normalizeWhitespace(line.substring(0, line.length() - 1).trim())));
        }
        return signatures;
    }

    private static String normalizeTopSuffix(String name) {
        return name.replaceAll("_Top\\d+$", "");
    }

    private static java.util.Map<String, String> extractTestMethods(String source) {
        List<String> lines = source.lines().collect(java.util.stream.Collectors.toList());
        java.util.Map<String, String> methods = new java.util.TreeMap<>();
        boolean pendingTest = false;
        for (int i = 0; i < lines.size(); i++) {
            String line = lines.get(i).trim();
            if (line.contains("@Test")) {
                pendingTest = true;
                continue;
            }
            if (!pendingTest) {
                continue;
            }
            if (!line.endsWith("{")) {
                continue;
            }
            String methodName = extractMethodNameFromSignature(line);
            if (methodName.isEmpty()) {
                pendingTest = false;
                continue;
            }
            StringBuilder block = new StringBuilder();
            int braceCount = 0;
            for (int j = i; j < lines.size(); j++) {
                String raw = lines.get(j);
                block.append(raw).append("\n");
                braceCount += countChar(raw, '{');
                braceCount -= countChar(raw, '}');
                if (braceCount == 0) {
                    i = j;
                    break;
                }
            }
            methods.put(methodName, normalizeWhitespace(block.toString()));
            pendingTest = false;
        }
        return methods;
    }

    private static String extractMethodNameFromSignature(String line) {
        int paren = line.indexOf('(');
        if (paren == -1) return "";
        String before = line.substring(0, paren).trim();
        String[] parts = before.split("\\s+");
        if (parts.length == 0) return "";
        return parts[parts.length - 1];
    }

    private static int countChar(String line, char c) {
        int count = 0;
        for (int i = 0; i < line.length(); i++) {
            if (line.charAt(i) == c) count++;
        }
        return count;
    }

    private static String normalizeWhitespace(String text) {
        return text.replaceAll("\\s+", " ").trim();
    }
}
