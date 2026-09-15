package runner;

import model.TestId;
import org.junit.platform.engine.discovery.DiscoverySelectors;
import org.junit.platform.launcher.Launcher;
import org.junit.platform.launcher.LauncherDiscoveryRequest;
import org.junit.platform.launcher.EngineFilter;
import org.junit.platform.launcher.core.LauncherDiscoveryRequestBuilder;
import org.junit.platform.launcher.core.LauncherFactory;
import org.junit.platform.launcher.core.LauncherConfig;
import org.junit.platform.launcher.listeners.SummaryGeneratingListener;
import org.junit.platform.launcher.listeners.TestExecutionSummary;

import java.util.List;
import java.util.Map;
import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;

public final class JUnit5TestRunner {

    private final ClassLoader classLoader;
    private final Launcher launcher;

    public JUnit5TestRunner(ClassLoader classLoader) {
        this.classLoader = classLoader;
        this.launcher = LauncherFactory.create(
                LauncherConfig.builder()
                        .enableTestExecutionListenerAutoRegistration(false)
                        .build()
        );
    }

    public void runTests(List<TestId> tests) {
        Map<String, List<TestId>> byClass = tests.stream()
                .collect(Collectors.groupingBy(TestId::getClassName));

        long timeoutMs = TestTimeouts.resolveTimeoutMs();
        ExecutorService executor = TestTimeouts.newExecutor("junit5-runner");
        long totalStarted = 0;
        long totalFailed = 0;
        long totalTimeout = 0;

        try {
            for (Map.Entry<String, List<TestId>> e : byClass.entrySet()) {
                Class<?> testClass = loadClass(e.getKey());

                for (TestId t : e.getValue()) {
                    LauncherDiscoveryRequest request;

                    if (t.isClassOnly()) {
                        request = LauncherDiscoveryRequestBuilder.request()
                                .selectors(DiscoverySelectors.selectClass(testClass))
                                .filters(EngineFilter.includeEngines("junit-jupiter"))
                                .build();
                    } else {
                        Method selectedMethod = findMethod(testClass, t.getMethodName());
                        request = LauncherDiscoveryRequestBuilder.request()
                                .selectors(DiscoverySelectors.selectMethod(testClass, selectedMethod))
                                .filters(EngineFilter.includeEngines("junit-jupiter"))
                                .build();
                    }

                    SummaryGeneratingListener summary = new SummaryGeneratingListener();
                    launcher.registerTestExecutionListeners(summary);

                    String selector = t.isClassOnly()
                            ? t.getClassName()
                            : t.getClassName() + "#" + t.getMethodName();
                    TestExecutionSummary s = runWithTimeout(executor, timeoutMs, selector, () -> {
                        launcher.execute(request);
                        return summary.getSummary();
                    });

                    if (s != null) {
                        totalStarted += s.getTestsStartedCount();
                        totalFailed += s.getTestsFailedCount();
                        System.out.println("[JUnit5TestRunner] started=" + s.getTestsStartedCount()
                                + " succeeded=" + s.getTestsSucceededCount()
                                + " failed=" + s.getTestsFailedCount()
                                + " skipped=" + s.getTestsSkippedCount());
                        if (s.getTestsFailedCount() > 0) {
                            printFailures(selector, s);
                        }
                    } else {
                        totalTimeout += 1;
                    }
                }
            }
        } finally {
            executor.shutdownNow();
        }
        if (totalStarted == 0 || totalFailed > 0 || totalTimeout > 0) {
            throw new AssertionError(
                    "JUnit 5 execution was not successful: started=" + totalStarted
                            + " failed=" + totalFailed + " timeout=" + totalTimeout
            );
        }
    }

    private TestExecutionSummary runWithTimeout(ExecutorService executor,
                                                long timeoutMs,
                                                String selector,
                                                java.util.concurrent.Callable<TestExecutionSummary> task) {
        if (timeoutMs <= 0) {
            try {
                return task.call();
            } catch (Exception e) {
                throw new RuntimeException("Test failed: " + selector, e);
            }
        }
        Future<TestExecutionSummary> f = executor.submit(task);
        try {
            return f.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            f.cancel(true);
            System.out.println("[JUnit5TestRunner] TIMEOUT after " + timeoutMs + "ms: " + selector);
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

    private Method findMethod(Class<?> testClass, String methodName) {
        Method selected = null;
        for (Method method : testClass.getDeclaredMethods()) {
            if (!method.getName().equals(methodName)) {
                continue;
            }
            if (selected != null) {
                throw new IllegalArgumentException(
                        "Ambiguous overloaded test method: " + testClass.getName() + "#" + methodName
                );
            }
            selected = method;
        }
        if (selected == null) {
            throw new IllegalArgumentException(
                    "Test method not found: " + testClass.getName() + "#" + methodName
            );
        }
        return selected;
    }

    private void printFailures(String selector, TestExecutionSummary summary) {
        for (TestExecutionSummary.Failure failure : summary.getFailures()) {
            String testDisplay = failure.getTestIdentifier().getDisplayName();
            Throwable ex = failure.getException();
            String reason = ex != null ? ex.toString() : "Unknown failure";
            System.out.println("[JUnit5TestRunner] FAILURE in " + selector
                    + " -> " + testDisplay + " :: " + reason);
        }
    }
}
