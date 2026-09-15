package custom_runner;

import org.junit.platform.engine.TestExecutionResult;
import org.junit.platform.engine.discovery.DiscoverySelectors;
import org.junit.platform.launcher.*;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;
import org.junit.platform.launcher.listeners.SummaryGeneratingListener;
import org.junit.platform.launcher.listeners.TestExecutionSummary;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public final class CustomRunnerJUnit5 {

    private static final boolean FULL_STACK_TRACES =
            Boolean.getBoolean("customrunner.fullStackTraces");

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            System.err.println("Usage: java custom_runner.CustomRunnerJupiter <test-class1> <test-class2> ...");
            System.exit(1);
        }

        LauncherDiscoveryRequestBuilder builder = LauncherDiscoveryRequestBuilder.request();
        for (String className : args) {
            builder.selectors(DiscoverySelectors.selectClass(className));
        }
        LauncherDiscoveryRequest request = builder.build();

        Launcher launcher = LauncherFactory.create();

        VerboseJupiterListener verbose = new VerboseJupiterListener();
        SummaryGeneratingListener summary = new SummaryGeneratingListener();

        launcher.registerTestExecutionListeners(verbose, summary);
        launcher.execute(request);

        TestExecutionSummary s = summary.getSummary();

        long run = s.getTestsStartedCount();
        long failed = s.getTestsFailedCount();
        long skipped = s.getTestsSkippedCount();
        long aborted = s.getTestsAbortedCount();
        long passed = run - failed - skipped - aborted;

        System.out.println();
        System.out.println("====== TEST SUMMARY ======");
        System.out.println("Run:     " + run);
        System.out.println("Passed:  " + passed);
        System.out.println("Failed:  " + failed);
        System.out.println("Skipped: " + skipped);
        System.out.println("Aborted: " + aborted);

        if (failed > 0 && !FULL_STACK_TRACES) {
            System.out.println();
            System.out.println("Note: stack traces collapsed. Run with");
            System.out.println("  -Dcustomrunner.fullStackTraces=true");
            System.out.println("to see full stack traces for failures.");
            System.exit(1);
        }

        if (failed > 0) System.exit(1);
    }

    private static final class VerboseJupiterListener implements TestExecutionListener {

        private final Map<String, Long> startTimesNs = new ConcurrentHashMap<>();
        private final Map<String, ByteArrayOutputStream> capturedOutput = new ConcurrentHashMap<>();

        private volatile PrintStream originalOut;
        private volatile PrintStream originalErr;

        private String key(TestIdentifier id) {
            // Unique per test
            return id.getUniqueId();
        }

        private String displayName(TestIdentifier id) {
            // Prefer source-based name: Class#method
            return id.getDisplayName();
        }

        @Override
        public void executionStarted(TestIdentifier testIdentifier) {
            if (!testIdentifier.isTest()) return;

            System.out.println("\n>>> START  " + displayName(testIdentifier));
            startTimesNs.put(key(testIdentifier), System.nanoTime());

            // Capture output for this test
            originalOut = System.out;
            originalErr = System.err;

            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            PrintStream ps = new PrintStream(buffer);
            System.setOut(ps);
            System.setErr(ps);

            capturedOutput.put(key(testIdentifier), buffer);
        }

        @Override
        public void executionFinished(TestIdentifier testIdentifier, TestExecutionResult testExecutionResult) {
            if (!testIdentifier.isTest()) return;

            long start = startTimesNs.getOrDefault(key(testIdentifier), System.nanoTime());
            double durationMs = (System.nanoTime() - start) / 1_000_000.0;

            // Restore stdout/stderr
            System.setOut(originalOut);
            System.setErr(originalErr);

            // Print captured output
            ByteArrayOutputStream buffer = capturedOutput.get(key(testIdentifier));
            if (buffer != null) {
                String logs = buffer.toString();
                if (!logs.isBlank()) {
                    System.out.println("----- LOG OUTPUT BEGIN -----");
                    System.out.print(logs);
                    System.out.println("------ LOG OUTPUT END ------");
                }
            }

            if (testExecutionResult.getStatus() == TestExecutionResult.Status.SUCCESSFUL) {
                System.out.printf("<<< PASS   %s [%.3f ms]%n", displayName(testIdentifier), durationMs);
                return;
            }

            if (testExecutionResult.getStatus() == TestExecutionResult.Status.ABORTED) {
                System.out.printf("<<< ABORT  %s [%.3f ms]%n", displayName(testIdentifier), durationMs);
                testExecutionResult.getThrowable().ifPresent(this::printThrowableCollapsedOrFull);
                return;
            }

            // FAILED
            System.out.printf("<<< FAIL   %s [%.3f ms]%n", displayName(testIdentifier), durationMs);
            testExecutionResult.getThrowable().ifPresent(this::printThrowableCollapsedOrFull);
        }

        private void printThrowableCollapsedOrFull(Throwable ex) {
            if (FULL_STACK_TRACES) {
                System.out.println("----- STACK TRACE BEGIN -----");
                ex.printStackTrace(System.out);
                System.out.println("------ STACK TRACE END ------");
            } else {
                System.out.println("REASON: " + ex);
            }
        }
    }
}