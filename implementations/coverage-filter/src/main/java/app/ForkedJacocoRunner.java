package app;

import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.jar.JarFile;

public final class ForkedJacocoRunner {

    private static final long FORK_EXIT_GRACE_MS = 10_000L;
    private static final long FORK_TERMINATION_GRACE_MS = 2_000L;

    private final String jacocoAgentJar;
    private final File libsDir;
    private final String sutClassesPath;
    private final String testClassesPath;
    private final String toolJarPath;
    private final String runOneMainClass;

    public ForkedJacocoRunner(String jacocoAgentJar,
                              File libsDir,
                              String sutClassesPath,
                              String testClassesPath,
                              String toolJarPath,
                              String runOneMainClass) {
        this.jacocoAgentJar = Objects.requireNonNull(jacocoAgentJar);
        this.libsDir = Objects.requireNonNull(libsDir);
        this.sutClassesPath = Objects.requireNonNull(sutClassesPath);
        this.testClassesPath = Objects.requireNonNull(testClassesPath);
        this.toolJarPath = Objects.requireNonNull(toolJarPath);
        this.runOneMainClass = Objects.requireNonNull(runOneMainClass);
    }

    public void runTestClass(String testClassFqcn, File execFile, boolean append) throws Exception {
        Objects.requireNonNull(testClassFqcn);
        Objects.requireNonNull(execFile);

        if (!append && execFile.exists()) execFile.delete();
        File parent = execFile.getParentFile();
        if (parent != null) parent.mkdirs();

        List<String> cmd = new ArrayList<>();
        cmd.add("java");

        addTimeoutProperty(cmd);
        addMacHeadlessProperties(cmd);
        addLoggingProviderProperty(cmd);
        addRuntimeCompatibilityOptions(cmd);

        cmd.add("--add-opens"); cmd.add("java.base/java.lang=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.lang.reflect=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.util=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.net=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.desktop/java.awt=ALL-UNNAMED");

        if (hasJbossLogmanager()) {
            cmd.add("-Djava.util.logging.manager=org.jboss.logmanager.LogManager");
        }

        cmd.add(buildJacocoAgentArg(execFile, append));

        cmd.add("-cp");
        cmd.add(buildClasspath());

        cmd.add(runOneMainClass);
        cmd.add(testClassFqcn);

        Process p = processBuilder(cmd).inheritIO().start();
        int exit = waitForTestFork(p, 1, testClassFqcn);
        if (exit != 0) throw new RuntimeException("Fork failed (exit=" + exit + "): " + testClassFqcn);
    }

    private String buildClasspath() {
        String sep = System.getProperty("os.name", "").toLowerCase().contains("win") ? ";" : ":";

        List<String> entries = new ArrayList<>();
        // RQ1 compares variants against the frozen SUT bytecode.  It must precede both
        // freshly compiled test/runtime classes and generated runtime-library JARs, either
        // of which may contain a second copy of the CUT with a different JaCoCo identity.
        // Target-specific incompatibilities must use an explicit run configuration or an
        // accepted manual measurement, never a global classpath-precedence change.
        entries.add(sutClassesPath);
        entries.add(testClassesPath);
        String runtimeSutClasspath = System.getProperty("covfilter.runtimeSutCp", "");
        if (!runtimeSutClasspath.isBlank()) {
            for (String entry : runtimeSutClasspath.split(java.util.regex.Pattern.quote(sep))) {
                if (!entry.isBlank() && !isConflictingJunitRuntimeEntry(entry)
                        && !entries.contains(entry)) {
                    entries.add(entry);
                }
            }
        }
        // The tool JAR embeds parser/runtime dependencies.  Keep it after the
        // frozen project's runtime so those embedded copies cannot shadow the
        // versions against which the project and its tests were built.
        entries.add(toolJarPath);
        for (String entry : listJars(libsDir)) {
            if (!isConflictingJunitRuntimeEntry(entry)) {
                entries.add(entry);
            }
        }

        return String.join(sep, entries);
    }

    public void runSelectors(List<String> selectors, File execFile, boolean append) throws Exception {
        int exit = runSelectorsAllowingFailure(selectors, execFile, append);
        if (exit != 0) {
            throw new RuntimeException("Fork failed (exit=" + exit + "): selectors=" + selectors);
        }
    }

    public int runSelectorsAllowingFailure(List<String> selectors, File execFile, boolean append) throws Exception {
        Objects.requireNonNull(selectors, "selectors");
        if (selectors.isEmpty()) throw new IllegalArgumentException("selectors is empty");
        Objects.requireNonNull(execFile, "execFile");

        if (!append && execFile.exists()) {
            //noinspection ResultOfMethodCallIgnored
            execFile.delete();
        }
        File parent = execFile.getParentFile();
        if (parent != null) parent.mkdirs();

        List<String> cmd = new ArrayList<>();
        cmd.add("java");

        addTimeoutProperty(cmd);
        addMacHeadlessProperties(cmd);
        addLoggingProviderProperty(cmd);
        addRuntimeCompatibilityOptions(cmd);

        cmd.add("--add-opens"); cmd.add("java.base/java.lang=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.lang.reflect=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.util=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.net=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.desktop/java.awt=ALL-UNNAMED");

        if (hasJbossLogmanager()) {
            cmd.add("-Djava.util.logging.manager=org.jboss.logmanager.LogManager");
        }

        cmd.add(buildJacocoAgentArg(execFile, append));

        cmd.add("-cp");
        cmd.add(buildClasspath());

        // Use RunMany now
        cmd.add("app.RunMany");
        cmd.addAll(selectors);

        Process p = processBuilder(cmd).inheritIO().start();
        return waitForTestFork(p, selectors.size(), "selectors=" + selectors);
    }

    private int waitForTestFork(Process process, int selectorCount, String description) throws Exception {
        long perSelectorTimeoutMs = runner.TestTimeouts.resolveTimeoutMs();
        if (perSelectorTimeoutMs <= 0) {
            return process.waitFor();
        }

        long selectorBudgetMs;
        try {
            selectorBudgetMs = Math.multiplyExact(perSelectorTimeoutMs, Math.max(1, selectorCount));
        } catch (ArithmeticException overflow) {
            selectorBudgetMs = Long.MAX_VALUE - FORK_EXIT_GRACE_MS;
        }
        long forkTimeoutMs = selectorBudgetMs > Long.MAX_VALUE - FORK_EXIT_GRACE_MS
                ? Long.MAX_VALUE
                : selectorBudgetMs + FORK_EXIT_GRACE_MS;

        try {
            if (process.waitFor(forkTimeoutMs, TimeUnit.MILLISECONDS)) {
                return process.exitValue();
            }
        } catch (InterruptedException interrupted) {
            terminateProcessTree(process);
            Thread.currentThread().interrupt();
            throw interrupted;
        }

        terminateProcessTree(process);
        throw new RuntimeException(
                "Fork timed out after " + forkTimeoutMs + " ms: " + description
        );
    }

    private void terminateProcessTree(Process process) {
        List<ProcessHandle> descendants = process.toHandle().descendants().toList();
        descendants.forEach(ProcessHandle::destroy);
        process.destroy();

        try {
            process.waitFor(FORK_TERMINATION_GRACE_MS, TimeUnit.MILLISECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }

        descendants.stream()
                .filter(ProcessHandle::isAlive)
                .forEach(ProcessHandle::destroyForcibly);
        if (process.isAlive()) {
            process.destroyForcibly();
        }
    }

    private List<String> listJars(File dir) {
        if (!dir.isDirectory()) {
            throw new IllegalArgumentException("libsDir is not a directory: " + dir.getPath());
        }

        File[] jars = dir.listFiles((d, name) -> name.endsWith(".jar"));
        if (jars == null) return List.of();

        Arrays.sort(jars, Comparator.comparing(File::getName));
        List<String> paths = new ArrayList<>(jars.length);
        for (File f : jars) paths.add(f.getPath());
        return paths;
    }

    public List<String> runAndCaptureLines(String mainClass, List<String> args) throws Exception {
        List<String> cmd = new ArrayList<>();
        cmd.add("java");

        addTimeoutProperty(cmd);
        addMacHeadlessProperties(cmd);
        addLoggingProviderProperty(cmd);
        addRuntimeCompatibilityOptions(cmd);

        cmd.add("--add-opens"); cmd.add("java.base/java.lang=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.lang.reflect=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.util=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.base/java.net=ALL-UNNAMED");
        cmd.add("--add-opens"); cmd.add("java.desktop/java.awt=ALL-UNNAMED");

        if (hasJbossLogmanager()) {
            cmd.add("-Djava.util.logging.manager=org.jboss.logmanager.LogManager");
        }

        cmd.add("-cp");
        cmd.add(buildClasspath());

        cmd.add(mainClass);
        cmd.addAll(args);

        ProcessBuilder pb = processBuilder(cmd);
        pb.redirectErrorStream(true);
        Process p = pb.start();

        List<String> lines = new ArrayList<>();
        try (java.io.BufferedReader br = new java.io.BufferedReader(
                new java.io.InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = br.readLine()) != null) {
                lines.add(line);
            }
        }

        int exit = p.waitFor();
        if (exit != 0) {
            int from = Math.max(0, lines.size() - 40);
            String output = String.join(System.lineSeparator(), lines.subList(from, lines.size()));
            throw new RuntimeException("Fork failed (exit=" + exit + ") main=" + mainClass
                    + (output.isBlank() ? "" : System.lineSeparator() + output));
        }
        return lines;
    }

    private ProcessBuilder processBuilder(List<String> cmd) {
        ProcessBuilder builder = new ProcessBuilder(cmd);
        String userDir = System.getProperty("user.dir");
        if (userDir != null && !userDir.isBlank()) {
            File workingDirectory = new File(userDir);
            if (workingDirectory.isDirectory()) {
                builder.directory(workingDirectory);
            }
        }
        return builder;
    }

    private void addTimeoutProperty(List<String> cmd) {
        String timeoutMs = System.getProperty(runner.TestTimeouts.TIMEOUT_PROP);
        if (timeoutMs != null && !timeoutMs.isBlank()) {
            cmd.add("-D" + runner.TestTimeouts.TIMEOUT_PROP + "=" + timeoutMs.trim());
        }
    }

    private void addMacHeadlessProperties(List<String> cmd) {
        if (!isMacOs()) {
            return;
        }
        // Enabled by default on macOS to prevent foreground UI popups from forked test JVMs.
        boolean enabled = Boolean.parseBoolean(System.getProperty("fork.macos.headless", "true"));
        if (!enabled) {
            return;
        }
        cmd.add("-Djava.awt.headless=true");
        cmd.add("-Dapple.awt.UIElement=true");
    }

    private boolean isMacOs() {
        String os = System.getProperty("os.name", "");
        return os.toLowerCase().contains("mac");
    }

    private String buildJacocoAgentArg(File execFile, boolean append) {
        StringBuilder sb = new StringBuilder();
        sb.append("-javaagent:").append(jacocoAgentJar)
                .append("=destfile=").append(execFile.getPath())
                .append(",append=").append(append);
        String includes = System.getProperty("jacoco.includes");
        if (includes != null && !includes.isBlank()) {
            sb.append(",includes=").append(includes.trim());
        }
        return sb.toString();
    }

    private boolean hasJbossLogmanager() {
        if (!libsDir.isDirectory()) {
            return false;
        }
        File[] jars = libsDir.listFiles((d, name) ->
                name != null && name.startsWith("jboss-logmanager") && name.endsWith(".jar"));
        return jars != null && jars.length > 0;
    }

    private void addLoggingProviderProperty(List<String> cmd) {
        if (!libsDir.isDirectory()) {
            return;
        }
        File jettyProvider = new File(libsDir, "jetty-native-slf4j-provider-service.jar");
        if (jettyProvider.isFile()) {
            cmd.add("-Dslf4j.provider=org.eclipse.jetty.logging.JettyLoggingServiceProvider");
            return;
        }
        File[] jars = libsDir.listFiles((d, name) ->
                name != null && name.startsWith("logback-classic-") && name.endsWith(".jar"));
        if (jars != null && jars.length > 0) {
            cmd.add("-Dslf4j.provider=ch.qos.logback.classic.spi.LogbackServiceProvider");
        }
    }

    static boolean isConflictingJunitRuntimeEntry(String entry) {
        String normalized = entry.replace('\\', '/').toLowerCase();
        String fileName = new File(entry).getName().toLowerCase();
        return normalized.contains("/org/junit/platform/")
                || normalized.contains("/org/junit/jupiter/")
                || normalized.contains("/org/opentest4j/")
                || normalized.contains("/org/apiguardian/")
                || fileName.startsWith("junit-platform-")
                || fileName.startsWith("junit-jupiter-")
                || fileName.startsWith("junit-vintage-")
                || fileName.startsWith("opentest4j-")
                || fileName.startsWith("apiguardian-");
    }

    private void addRuntimeCompatibilityOptions(List<String> cmd) {
        cmd.add("-XX:+EnableDynamicAgentLoading");
        cmd.add("-Dnet.bytebuddy.experimental=true");
        List<File> candidates = new ArrayList<>();
        String runtimeClasspath = System.getProperty("covfilter.runtimeSutCp", "");
        if (!runtimeClasspath.isBlank()) {
            for (String entry : runtimeClasspath.split(
                    java.util.regex.Pattern.quote(File.pathSeparator))) {
                File candidate = new File(entry);
                if (candidate.isFile() && candidate.getName().startsWith("byte-buddy-agent-")
                        && candidate.getName().endsWith(".jar")) {
                    candidates.add(candidate);
                }
            }
        }
        File[] frozenAgents = libsDir.listFiles((directory, name) ->
                name != null && name.startsWith("byte-buddy-agent-") && name.endsWith(".jar"));
        if (frozenAgents != null) {
            Arrays.sort(frozenAgents, Comparator.comparing(File::getName).reversed());
            candidates.addAll(Arrays.asList(frozenAgents));
        }
        File agent = firstPremainJar(candidates);
        if (agent != null) {
            cmd.add("-javaagent:" + agent.getPath());
        }
    }

    static File firstPremainJar(List<File> candidates) {
        return candidates.stream().filter(ForkedJacocoRunner::hasPremainClass)
                .findFirst().orElse(null);
    }

    static boolean hasPremainClass(File jar) {
        try (JarFile archive = new JarFile(jar)) {
            return archive.getManifest() != null
                    && archive.getManifest().getMainAttributes().getValue("Premain-Class") != null;
        } catch (Exception ignored) {
            return false;
        }
    }
}
