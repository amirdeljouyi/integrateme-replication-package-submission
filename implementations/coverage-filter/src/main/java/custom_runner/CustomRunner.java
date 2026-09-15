package custom_runner;

import org.junit.runner.JUnitCore;
import org.junit.runner.Result;
import org.junit.runner.notification.RunListener;
import org.junit.runner.notification.Failure;
import org.junit.runner.Description;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.util.HashMap;
import java.util.Map;

public class CustomRunner {

    private static final boolean FULL_STACK_TRACES =
            Boolean.getBoolean("customrunner.fullStackTraces");

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            System.err.println("Usage: java CustomRunnerVerbose <test-class1> <test-class2> ...");
            System.exit(1);
        }

        Class<?>[] classes = new Class<?>[args.length];
        for (int i = 0; i < args.length; i++) {
            classes[i] = Class.forName(args[i]);
        }

        JUnitCore core = new JUnitCore();
        core.addListener(new VerboseListener());

        Result result = core.run(classes);

        int run     = result.getRunCount();
        int failed  = result.getFailureCount();
        int ignored = result.getIgnoreCount();
        int passed  = run - failed - ignored;

        System.out.println();
        System.out.println("====== TEST SUMMARY ======");
        System.out.println("Run:     " + run);
        System.out.println("Passed:  " + passed);
        System.out.println("Failed:  " + failed);
        System.out.println("Ignored: " + ignored);

        if (!result.getFailures().isEmpty() && !FULL_STACK_TRACES) {
            System.out.println();
            System.out.println("Note: stack traces collapsed. Run with");
            System.out.println("  -Dcustomrunner.fullStackTraces=true");
            System.out.println("to see full stack traces for failures.");
        }
    }

    private static class VerboseListener extends RunListener {
        private final Map<Description, Long> startTimes = new HashMap<>();
        private final Map<Description, ByteArrayOutputStream> capturedOutput = new HashMap<>();
        private PrintStream originalOut;
        private PrintStream originalErr;

        // Helper to clean names
        private String displayName(Description d) {
            return d.getClassName() + "#" + d.getMethodName();
        }

        @Override
        public void testStarted(Description description) {
            System.out.println("\n>>> START  " + displayName(description));

            startTimes.put(description, System.nanoTime());

            // Capture output
            originalOut = System.out;
            originalErr = System.err;

            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            PrintStream ps = new PrintStream(buffer);
            System.setOut(ps);
            System.setErr(ps);

            capturedOutput.put(description, buffer);
        }

        @Override
        public void testFinished(Description description) {
            long durationNs = System.nanoTime() - startTimes.get(description);
            double durationMs = durationNs / 1_000_000.0;

            // Restore stdout/stderr
            System.setOut(originalOut);
            System.setErr(originalErr);

            // Print captured output
            String logs = capturedOutput.get(description).toString();
            if (!logs.isBlank()) {
                System.out.println("----- LOG OUTPUT BEGIN -----");
                System.out.print(logs);
                System.out.println("------ LOG OUTPUT END ------");
            }

            System.out.printf("<<< PASS   %s [%.3f ms]%n", displayName(description), durationMs);
        }

        @Override
        public void testFailure(Failure failure) {
            Description description = failure.getDescription();
            long durationNs = System.nanoTime() - startTimes.get(description);
            double durationMs = durationNs / 1_000_000.0;

            // Restore stdout/stderr
            System.setOut(originalOut);
            System.setErr(originalErr);

            // Print captured test logs
            String logs = capturedOutput.get(description).toString();
            if (!logs.isBlank()) {
                System.out.println("----- LOG OUTPUT BEGIN -----");
                System.out.print(logs);
                System.out.println("------ LOG OUTPUT END ------");
            }

            System.out.printf("<<< FAIL   %s [%.3f ms]%n", displayName(description), durationMs);
            Throwable ex = failure.getException();
            if (FULL_STACK_TRACES && ex != null) {
                System.out.println("----- STACK TRACE BEGIN -----");
                ex.printStackTrace(System.out);   // full stack trace
                System.out.println("------ STACK TRACE END ------");
            } else {
                System.out.println("REASON: " + failure.getMessage());
            }
        }

        @Override
        public void testIgnored(Description description) {
            System.out.println();
            System.out.println("<<< IGNORED " + displayName(description));
        }
    }
}