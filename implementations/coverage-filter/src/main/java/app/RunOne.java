package app;

import runner.JUnit5TestRunner;
import model.TestId;
import runner.TestTimeouts;

import java.util.List;

public final class RunOne {
    public static void main(String[] args) throws Exception {
        String testClass;
        if (args.length == 1) {
            testClass = args[0];
        } else if (args.length == 2 && args[0].startsWith("--timeout-ms=")) {
            String raw = args[0].substring("--timeout-ms=".length());
            System.setProperty(TestTimeouts.TIMEOUT_PROP, raw);
            testClass = args[1];
        } else if (args.length == 3 && "--timeout-ms".equals(args[0])) {
            System.setProperty(TestTimeouts.TIMEOUT_PROP, args[1]);
            testClass = args[2];
        } else {
            throw new IllegalArgumentException("Usage: RunOne [--timeout-ms <ms>] <testClassFqcn>");
        }

        // inside RunOne.main, before running
        Class<?> c = Class.forName(testClass, true, Thread.currentThread().getContextClassLoader());
        System.out.println("[RunOne] Loaded test class: " + testClass);

        TestDetector.JUnitVersion version = TestDetector.detect(c);
        if (version == TestDetector.JUnitVersion.JUNIT_4) {
            runner.JUnit4TestRunner r = new runner.JUnit4TestRunner(Thread.currentThread().getContextClassLoader());
            r.runTests(List.of(new TestId(testClass, null)));
        } else if (version == TestDetector.JUnitVersion.TESTNG) {
            runner.TestNgTestRunner r = new runner.TestNgTestRunner(Thread.currentThread().getContextClassLoader());
            r.runTests(List.of(new TestId(testClass, null)));
        } else {
            runner.JUnit5TestRunner r = new runner.JUnit5TestRunner(Thread.currentThread().getContextClassLoader());
            r.runTests(List.of(new TestId(testClass, null))); // class-level
        }
    }
}
