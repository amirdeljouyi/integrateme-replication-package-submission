package io;

import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.declaration.*;
import spoon.reflect.reference.CtTypeReference;
import jacoco.TestDelta;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import spoon.reflect.cu.position.NoSourcePosition;

public final class TopNReducedTestClassGenerator {

    public void generateReducedClass(File originalTestSource,
                                     List<TestDelta> rankedTests,
                                     int topN,
                                     File outputRootDir) {

        Launcher launcher = new Launcher();
        launcher.getEnvironment().setComplianceLevel(11);
        launcher.getEnvironment().setAutoImports(false);

        // 🔑 Tell Spoon where to write generated sources
        launcher.setSourceOutputDirectory(outputRootDir);

        launcher.addInputResource(originalTestSource.getAbsolutePath());
        launcher.buildModel();

        CtModel model = launcher.getModel();

        CtClass<?> originalClass = model.getAllTypes().stream()
                .filter(t -> t instanceof CtClass<?>)
                .map(t -> (CtClass<?>) t)
                .findFirst()
                .orElseThrow(() ->
                        new IllegalStateException("No class found in " + originalTestSource));

        // Top-N method names
        Set<String> selectedMethodNames =
                rankedTests.stream()
                        .limit(topN)
                        .map(TestDelta::getTestSelector)
                        .map(s -> s.substring(s.indexOf('#') + 1))
                        .collect(Collectors.toCollection(LinkedHashSet::new));

        CtPackage pkg = originalClass.getPackage();
        String newClassName = originalClass.getSimpleName() + "_Top" + topN;

        CtClass<?> reduced = launcher.getFactory().Class().create(pkg, newClassName);
        @SuppressWarnings({"rawtypes", "unchecked"})
        CtClass reducedRaw = (CtClass) reduced;

        // same modifiers
        reduced.setModifiers(originalClass.getModifiers());

        // same superclass
        CtTypeReference<?> superClass = originalClass.getSuperclass();
        if (superClass != null) {
            reduced.setSuperclass(superClass.clone());
        }

        CtCompilationUnit originalCu = null;
        if (originalClass.getPosition() != null) {
            originalCu = originalClass.getPosition().getCompilationUnit();
        }
        if (originalCu != null) {
            CtCompilationUnit reducedCu = launcher.getFactory().CompilationUnit().getOrCreate(reduced);
            List<CtImport> imports = originalCu.getImports().stream()
                    .map(CtImport::clone)
                    .collect(Collectors.toList());
            reducedCu.setImports(imports);
        }

        // copy fields to preserve shared test fixtures
        for (CtField<?> field : originalClass.getFields()) {
            reducedRaw.addField((CtField) field.clone());
        }

        // copy constructors (often initialize helpers/fixtures)
        for (CtConstructor<?> ctor : originalClass.getConstructors()) {
            reducedRaw.addConstructor((CtConstructor) ctor.clone());
        }

        Set<String> testAnnotations = new HashSet<>(Arrays.asList(
                "Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"
        ));
        Set<String> lifecycleAnnotations = new HashSet<>(Arrays.asList(
                "BeforeEach", "AfterEach", "BeforeAll", "AfterAll",
                "Before", "After", "BeforeClass", "AfterClass"
        ));
        boolean junit3Class = isJUnit3TestCase(originalClass);

        // copy selected test methods + all helpers/lifecycle
        int copied = 0;
        List<CtMethod<?>> methods = new ArrayList<>(originalClass.getMethods());
        methods.sort(Comparator
                .comparingInt((CtMethod<?> m) -> {
                    if (m.getPosition() != null && m.getPosition().isValidPosition()) {
                        return m.getPosition().getLine();
                    }
                    return Integer.MAX_VALUE;
                })
                .thenComparing(CtMethod::getSimpleName));

        Map<String, CtMethod<?>> methodsByName = new HashMap<>();
        for (CtMethod<?> m : methods) {
            methodsByName.putIfAbsent(m.getSimpleName(), m);
        }

        for (CtMethod<?> m : methods) {
            boolean isTest = isTestMethod(m, testAnnotations, junit3Class);
            boolean isLifecycle = m.getAnnotations().stream()
                    .anyMatch(a -> lifecycleAnnotations.contains(a.getAnnotationType().getSimpleName()));

            if (isLifecycle) {
                CtMethod<?> clone = m.clone();
                clone.setPosition(new NoSourcePosition());
                reducedRaw.addMethod((CtMethod) clone);
                continue;
            }
            if (!isTest) {
                reducedRaw.addMethod((CtMethod) m.clone());
                continue;
            }
        }

        for (String methodName : selectedMethodNames) {
            CtMethod<?> m = methodsByName.get(methodName);
            if (m == null) continue;
            boolean isTest = isTestMethod(m, testAnnotations, junit3Class);
            if (!isTest) continue;
            CtMethod<?> clone = m.clone();
            clone.setPosition(new NoSourcePosition());
            reducedRaw.addMethod((CtMethod) clone);
            copied++;
        }

        if (copied == 0) {
            throw new IllegalStateException("No test methods copied — check CSV vs source.");
        }

        // 🔑 This writes the file correctly
        launcher.prettyprint();
        replaceImportsWithOriginal(originalTestSource, outputRootDir, pkg, newClassName);

        System.out.println("[Spoon] Generated reduced test class: "
                + pkg.getQualifiedName() + "." + newClassName
                + " (methods=" + copied + ")");
    }

    private static boolean isJUnit3TestCase(CtClass<?> testClass) {
        CtTypeReference<?> superclass = testClass.getSuperclass();
        while (superclass != null) {
            if ("junit.framework.TestCase".equals(superclass.getQualifiedName())
                    || "TestCase".equals(superclass.getSimpleName())) {
                return true;
            }
            CtType<?> declaration = superclass.getTypeDeclaration();
            superclass = declaration instanceof CtClass<?>
                    ? ((CtClass<?>) declaration).getSuperclass()
                    : null;
        }
        return false;
    }

    private static boolean isTestMethod(CtMethod<?> method,
                                        Set<String> testAnnotations,
                                        boolean junit3Class) {
        boolean annotated = method.getAnnotations().stream()
                .anyMatch(a -> testAnnotations.contains(a.getAnnotationType().getSimpleName()));
        return annotated || (
                junit3Class
                && method.hasModifier(ModifierKind.PUBLIC)
                && method.getSimpleName().startsWith("test")
                && method.getParameters().isEmpty()
                && method.getType() != null
                && "void".equals(method.getType().getSimpleName())
        );
    }

    private static void replaceImportsWithOriginal(File originalTestSource,
                                                   File outputRootDir,
                                                   CtPackage pkg,
                                                   String newClassName) {
        try {
            List<String> originalLines = Files.readAllLines(originalTestSource.toPath(), StandardCharsets.UTF_8);
            List<String> originalPrefix = extractPrefixBeforePackage(originalLines);
            List<String> originalImportBlock = extractImportBlock(originalLines);

            Path generatedPath = Path.of(outputRootDir.getPath(), packagePath(pkg), newClassName + ".java");
            List<String> generatedLines = new ArrayList<>(Files.readAllLines(generatedPath, StandardCharsets.UTF_8));

            int[] generatedRange = findImportBlockRange(generatedLines);
            if (originalImportBlock.isEmpty()) {
                if (generatedRange != null) {
                    generatedLines.subList(generatedRange[0], generatedRange[1] + 1).clear();
                }
            } else if (generatedRange != null) {
                generatedLines.subList(generatedRange[0], generatedRange[1] + 1).clear();
                generatedLines.addAll(generatedRange[0], originalImportBlock);
            } else {
                int insertAt = findPackageLineIndex(generatedLines);
                insertAt = insertAt == -1 ? 0 : insertAt + 1;
                generatedLines.addAll(insertAt, originalImportBlock);
            }

            if (!originalPrefix.isEmpty()) {
                int pkgIndex = findPackageLineIndex(generatedLines);
                if (pkgIndex >= 0) {
                    List<String> newLines = new ArrayList<>();
                    newLines.addAll(originalPrefix);
                    if (!originalPrefix.get(originalPrefix.size() - 1).isBlank()) {
                        newLines.add("");
                    }
                    newLines.addAll(generatedLines.subList(pkgIndex, generatedLines.size()));
                    generatedLines = newLines;
                }
            }

            if (hasBlankAfterPackage(originalLines)) {
                int pkgIndex = findPackageLineIndex(generatedLines);
                if (pkgIndex >= 0) {
                    if (pkgIndex + 1 >= generatedLines.size() || !generatedLines.get(pkgIndex + 1).isBlank()) {
                        generatedLines.add(pkgIndex + 1, "");
                    }
                }
            }

            Map<String, String> importMap = extractImportMap(originalImportBlock);
            Map<String, String> staticImportMap = extractStaticImportMap(originalImportBlock);
            int[] importRange = findImportBlockRange(generatedLines);
            for (int i = 0; i < generatedLines.size(); i++) {
                if (importRange != null && i >= importRange[0] && i <= importRange[1]) {
                    continue;
                }
                String line = generatedLines.get(i);
                for (Map.Entry<String, String> entry : importMap.entrySet()) {
                    String simple = entry.getKey();
                    String fqcn = entry.getValue();
                    line = line.replace("@" + fqcn, "@" + simple);
                    line = line.replace(fqcn + ".", simple + ".");
                    line = line.replaceAll("(?<![\\w.])" + Pattern.quote(fqcn) + "(?![\\w.])", simple);
                }
                for (Map.Entry<String, String> entry : staticImportMap.entrySet()) {
                    String target = entry.getKey();
                    String replacement = entry.getValue();
                    if (replacement.isEmpty()) {
                        line = line.replace(target, "");
                    } else {
                        line = line.replace(target, replacement);
                    }
                }
                // Prefer simple names for same-package type references in generated code.
                if (!line.contains("\"")) {
                    line = line.replace(pkg.getQualifiedName() + ".", "");
                }
                line = line.replace("java.lang.", "");
                if (line.contains("this.") && !line.contains("this(")) {
                    line = line.replace("this.", "");
                }
                generatedLines.set(i, line);
            }

            Files.write(generatedPath, generatedLines, StandardCharsets.UTF_8);
        } catch (Exception ignored) {
            // Leave generated output as-is if import rewrite fails.
        }
    }

    private static int findPackageLineIndex(List<String> lines) {
        for (int i = 0; i < lines.size(); i++) {
            if (lines.get(i).startsWith("package ")) {
                return i;
            }
        }
        return -1;
    }

    private static int[] findImportBlockRange(List<String> lines) {
        int start = -1;
        for (int i = 0; i < lines.size(); i++) {
            if (lines.get(i).startsWith("import ")) {
                start = i;
                break;
            }
        }
        if (start == -1) return null;
        int end = start;
        for (int i = start + 1; i < lines.size(); i++) {
            String line = lines.get(i);
            if (line.startsWith("import ") || line.isBlank()) {
                end = i;
                continue;
            }
            break;
        }
        return new int[]{start, end};
    }

    private static List<String> extractImportBlock(List<String> lines) {
        int[] range = findImportBlockRange(lines);
        if (range == null) return Collections.emptyList();
        return new ArrayList<>(lines.subList(range[0], range[1] + 1));
    }

    private static List<String> extractPrefixBeforePackage(List<String> lines) {
        int pkgIndex = findPackageLineIndex(lines);
        if (pkgIndex <= 0) return Collections.emptyList();
        return new ArrayList<>(lines.subList(0, pkgIndex));
    }

    private static boolean hasBlankAfterPackage(List<String> lines) {
        int pkgIndex = findPackageLineIndex(lines);
        if (pkgIndex == -1) return false;
        if (pkgIndex + 1 >= lines.size()) return false;
        return lines.get(pkgIndex + 1).isBlank();
    }

    private static Map<String, String> extractImportMap(List<String> importBlock) {
        Map<String, String> out = new HashMap<>();
        for (String line : importBlock) {
            if (!line.startsWith("import ") || line.startsWith("import static")) continue;
            String value = line.substring("import ".length()).trim();
            if (!value.endsWith(";")) continue;
            value = value.substring(0, value.length() - 1);
            if (value.endsWith(".*")) continue;
            int lastDot = value.lastIndexOf('.');
            if (lastDot <= 0) continue;
            String simple = value.substring(lastDot + 1);
            out.put(simple, value);
        }
        return out;
    }

    private static Map<String, String> extractStaticImportMap(List<String> importBlock) {
        Map<String, String> out = new HashMap<>();
        for (String line : importBlock) {
            if (!line.startsWith("import static ")) continue;
            String value = line.substring("import static ".length()).trim();
            if (!value.endsWith(";")) continue;
            value = value.substring(0, value.length() - 1);
            if (value.endsWith(".*")) {
                String prefix = value.substring(0, value.length() - 1);
                out.put(prefix, "");
                continue;
            }
            int lastDot = value.lastIndexOf('.');
            if (lastDot <= 0) continue;
            String methodName = value.substring(lastDot + 1);
            out.put(value, methodName);
        }
        return out;
    }

    private static String packagePath(CtPackage pkg) {
        if (pkg == null || pkg.isUnnamedPackage()) {
            return "";
        }
        return pkg.getQualifiedName().replace('.', File.separatorChar);
    }
}
