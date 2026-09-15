package app;

import model.TestId;
import runner.TestTimeouts;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public final class RunMany {

    public static void main(String[] args) {
        int exitCode = 0;
        try {
            run(args);
        } catch (Throwable failure) {
            failure.printStackTrace(System.err);
            exitCode = 1;
        } finally {
            System.out.flush();
            System.err.flush();
            // A timed-out test can leave non-daemon application threads behind.
            // Returning from main would keep this short-lived fork alive forever.
            System.exit(exitCode);
        }
    }

    static void run(String[] args) throws Exception {
        int argIndex = 0;
        if (args.length >= 1 && args[0].startsWith("--timeout-ms=")) {
            String raw = args[0].substring("--timeout-ms=".length());
            System.setProperty(TestTimeouts.TIMEOUT_PROP, raw);
            argIndex = 1;
        } else if (args.length >= 2 && "--timeout-ms".equals(args[0])) {
            System.setProperty(TestTimeouts.TIMEOUT_PROP, args[1]);
            argIndex = 2;
        }

        if (args.length - argIndex < 1) {
            throw new IllegalArgumentException(
                    "Usage: RunMany [--timeout-ms <ms>] <testSelector1> <testSelector2> ... (selector = fqcn or fqcn#method)"
            );
        }

        ClassLoader cl = Thread.currentThread().getContextClassLoader();

        // Build TestId list
        List<TestId> tests = new ArrayList<>();
        for (int i = argIndex; i < args.length; i++) {
            String s = args[i];
            TestId t = TestId.fromString(s); // supports fqcn or fqcn#method (your TestId already does)
            // fail fast if class not found
            Class.forName(t.getClassName(), true, cl);
            tests.add(t);
        }

        Map<String, List<TestId>> byClass = tests.stream()
                .collect(Collectors.groupingBy(TestId::getClassName));

        for (Map.Entry<String, List<TestId>> entry : byClass.entrySet()) {
            Class<?> c = Class.forName(entry.getKey(), true, cl);
            TestDetector.JUnitVersion version = TestDetector.detect(c);
            if (version == TestDetector.JUnitVersion.JUNIT_4) {
                new runner.JUnit4TestRunner(cl).runTests(entry.getValue());
            } else if (version == TestDetector.JUnitVersion.TESTNG) {
                new runner.TestNgTestRunner(cl).runTests(entry.getValue());
            } else {
                new runner.JUnit5TestRunner(cl).runTests(entry.getValue());
            }
        }
    }
}
