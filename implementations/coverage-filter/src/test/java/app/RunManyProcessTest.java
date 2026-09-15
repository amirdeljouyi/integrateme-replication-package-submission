package app;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

public class RunManyProcessTest {

    @Test
    public void exitsWhenTestLeavesNonDaemonThreadRunning() throws Exception {
        String javaExecutable = System.getProperty("java.home") + "/bin/java";
        String selector = LeakyTest.class.getName() + "#startsNonDaemonThread";
        Process process = new ProcessBuilder(
                javaExecutable,
                "-cp",
                System.getProperty("java.class.path"),
                RunMany.class.getName(),
                "--timeout-ms",
                "1000",
                selector
        ).redirectErrorStream(true).start();

        boolean exited = process.waitFor(10, TimeUnit.SECONDS);
        if (!exited) {
            process.destroyForcibly();
            process.waitFor(5, TimeUnit.SECONDS);
        }
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);

        assertTrue("RunMany fork did not exit. Output:\n" + output, exited);
        assertEquals("RunMany output:\n" + output, 0, process.exitValue());
    }

    @Test
    public void exitsNonzeroWhenJUnit4TestFails() throws Exception {
        ProcessResult result = runSelector(FailingJUnit4Test.class.getName() + "#fails");
        assertNotEquals("RunMany output:\n" + result.output, 0, result.exitCode);
        assertTrue(result.output.contains("failed=1"));
    }

    @Test
    public void exitsNonzeroWhenJUnit5TestFails() throws Exception {
        ProcessResult result = runSelector(FailingJUnit5Test.class.getName() + "#fails");
        assertNotEquals("RunMany output:\n" + result.output, 0, result.exitCode);
        assertTrue(result.output.contains("failed=1"));
    }

    @Test
    public void runsTestNgMethodWithTestNgRunner() throws Exception {
        ProcessResult result = runSelector(PassingTestNgTest.class.getName() + "#passes");
        assertEquals("RunMany output:\n" + result.output, 0, result.exitCode);
        assertTrue(result.output.contains("[TestNgTestRunner] run=1 failed=0"));
    }

    @Test
    public void runsParameterizedJUnit5MethodByReflectiveSignature() throws Exception {
        ProcessResult result = runSelector(
                ParameterizedJUnit5Test.class.getName() + "#acceptsInjectedParameter"
        );
        assertEquals("RunMany output:\n" + result.output, 0, result.exitCode);
        assertTrue(result.output.contains("started=2"));
    }

    private ProcessResult runSelector(String selector) throws Exception {
        String javaExecutable = System.getProperty("java.home") + "/bin/java";
        Process process = new ProcessBuilder(
                javaExecutable,
                "-cp",
                System.getProperty("java.class.path"),
                RunMany.class.getName(),
                "--timeout-ms",
                "1000",
                selector
        ).redirectErrorStream(true).start();
        boolean exited = process.waitFor(10, TimeUnit.SECONDS);
        if (!exited) {
            process.destroyForcibly();
            process.waitFor(5, TimeUnit.SECONDS);
        }
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertTrue("RunMany fork did not exit. Output:\n" + output, exited);
        return new ProcessResult(process.exitValue(), output);
    }

    private static final class ProcessResult {
        private final int exitCode;
        private final String output;

        private ProcessResult(int exitCode, String output) {
            this.exitCode = exitCode;
            this.output = output;
        }
    }

    public static class LeakyTest {
        @Test
        public void startsNonDaemonThread() {
            Thread leakedThread = new Thread(() -> {
                while (true) {
                    try {
                        Thread.sleep(60_000L);
                    } catch (InterruptedException ignored) {
                        // Deliberately emulate a test-owned thread that refuses shutdown.
                    }
                }
            }, "runmany-test-leaked-thread");
            leakedThread.setDaemon(false);
            leakedThread.start();
        }
    }

    public static class FailingJUnit4Test {
        @Test
        public void fails() {
            org.junit.Assert.fail("expected JUnit 4 failure");
        }
    }

    public static class FailingJUnit5Test {
        @org.junit.jupiter.api.Test
        public void fails() {
            org.junit.jupiter.api.Assertions.fail("expected JUnit 5 failure");
        }
    }

    public static class PassingTestNgTest {
        @org.testng.annotations.Test
        public void passes() {
            org.testng.Assert.assertTrue(true);
        }
    }

    public static class ParameterizedJUnit5Test {
        @org.junit.jupiter.params.ParameterizedTest
        @org.junit.jupiter.params.provider.ValueSource(ints = {1, 2})
        public void acceptsInjectedParameter(int value) {
            org.junit.jupiter.api.Assertions.assertTrue(value > 0);
        }
    }
}
