package runner;

import model.TestId;
import org.junit.runner.JUnitCore;
import org.junit.runner.Request;
import org.junit.runner.Result;
import org.junit.runner.notification.Failure;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;

public final class JUnit4TestRunner {

    private final ClassLoader classLoader;

    public JUnit4TestRunner(ClassLoader classLoader) {
        this.classLoader = classLoader;
    }

    public void runTests(List<TestId> tests) {
        Map<String, List<TestId>> byClass = tests.stream()
                .collect(Collectors.groupingBy(TestId::getClassName));

        JUnitCore core = new JUnitCore();
        int totalRun = 0;
        int totalFail = 0;
        int totalTimeout = 0;
        long timeoutMs = TestTimeouts.resolveTimeoutMs();
        ExecutorService executor = TestTimeouts.newExecutor("junit4-runner");

        try {
            for (Map.Entry<String, List<TestId>> e : byClass.entrySet()) {
                Class<?> testClass = loadClass(e.getKey());

                for (TestId t : e.getValue()) {
                    String selector = t.isClassOnly()
                            ? t.getClassName()
                            : t.getClassName() + "#" + t.getMethodName();
                    Result r = runWithTimeout(executor, timeoutMs, selector, () -> {
                        if (t.isClassOnly()) {
                            return core.run(testClass);
                        }
                        return core.run(Request.method(testClass, t.getMethodName()));
                    });
                    if (r != null) {
                        totalRun += r.getRunCount();
                        totalFail += r.getFailureCount();
                        if (r.getFailureCount() > 0) {
                            printFailures(selector, r.getFailures());
                        }
                    } else {
                        totalFail += 1;
                        totalTimeout += 1;
                    }
                }
            }
        } finally {
            executor.shutdownNow();
        }

        System.out.println("[JUnit4TestRunner] run=" + totalRun + " failed=" + totalFail
                + " timeout=" + totalTimeout);
        if (totalRun == 0 || totalFail > 0 || totalTimeout > 0) {
            throw new AssertionError(
                    "JUnit 4 execution was not successful: run=" + totalRun
                            + " failed=" + totalFail + " timeout=" + totalTimeout
            );
        }
    }

    private Result runWithTimeout(ExecutorService executor,
                                  long timeoutMs,
                                  String selector,
                                  java.util.concurrent.Callable<Result> task) {
        if (timeoutMs <= 0) {
            try {
                return task.call();
            } catch (Exception e) {
                throw new RuntimeException("Test failed: " + selector, e);
            }
        }
        Future<Result> f = executor.submit(task);
        try {
            return f.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            f.cancel(true);
            System.out.println("[JUnit4TestRunner] TIMEOUT after " + timeoutMs + "ms: " + selector);
            return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Test interrupted: " + selector, e);
        } catch (Exception e) {
            throw new RuntimeException("Test failed: " + selector, e);
        }
    }

    private Class<?> loadClass(String fqcn) {
        try {
            return Class.forName(fqcn, true, classLoader);
        } catch (ClassNotFoundException ex) {
            throw new RuntimeException("Cannot load test class: " + fqcn, ex);
        }
    }

    private void printFailures(String selector, List<Failure> failures) {
        for (Failure failure : failures) {
            Throwable ex = failure.getException();
            String testHeader = failure.getTestHeader();
            String reason = ex != null ? ex.toString() : failure.getMessage();
            System.out.println("[JUnit4TestRunner] FAILURE in " + selector
                    + " -> " + testHeader + " :: " + reason);
        }
    }
}
