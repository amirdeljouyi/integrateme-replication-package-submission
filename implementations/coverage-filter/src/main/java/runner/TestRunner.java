package runner;

import model.TestId;
import org.junit.runner.JUnitCore;
import org.junit.runner.Request;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;

public final class TestRunner {

    private final ClassLoader classLoader;

    public TestRunner(ClassLoader classLoader) {
        this.classLoader = classLoader;
    }

    public void runTests(List<TestId> tests) {
        Map<String, List<TestId>> byClass = tests.stream()
                .collect(Collectors.groupingBy(TestId::getClassName));

        JUnitCore core = new JUnitCore();
        long timeoutMs = TestTimeouts.resolveTimeoutMs();
        ExecutorService executor = TestTimeouts.newExecutor("test-runner");

        try {
            for (Map.Entry<String, List<TestId>> e : byClass.entrySet()) {
                Class<?> testClass = loadClass(e.getKey());
                for (TestId t : e.getValue()) {
                    Request req = Request.method(testClass, t.getMethodName());
                    runWithTimeout(executor, timeoutMs, t.getClassName() + "#" + t.getMethodName(), () -> {
                        core.run(req);
                        return null;
                    });
                }
            }
        } finally {
            executor.shutdownNow();
        }
    }

    private void runWithTimeout(ExecutorService executor,
                                long timeoutMs,
                                String selector,
                                java.util.concurrent.Callable<Void> task) {
        if (timeoutMs <= 0) {
            try {
                task.call();
            } catch (Exception e) {
                throw new RuntimeException("Test failed: " + selector, e);
            }
            return;
        }
        Future<Void> f = executor.submit(task);
        try {
            f.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            f.cancel(true);
            System.out.println("[TestRunner] TIMEOUT after " + timeoutMs + "ms: " + selector);
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
}
