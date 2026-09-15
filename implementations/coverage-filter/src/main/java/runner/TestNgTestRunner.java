package runner;

import model.TestId;
import org.testng.IMethodInstance;
import org.testng.ITestContext;
import org.testng.ITestResult;
import org.testng.TestListenerAdapter;
import org.testng.TestNG;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** Executes one TestNG method per forked coverage measurement. */
public final class TestNgTestRunner {

    private final ClassLoader classLoader;

    public TestNgTestRunner(ClassLoader classLoader) {
        this.classLoader = classLoader;
    }

    public void runTests(List<TestId> tests) {
        long timeoutMs = TestTimeouts.resolveTimeoutMs();
        ExecutorService executor = TestTimeouts.newExecutor("testng-runner");
        int totalRun = 0;
        int totalFail = 0;
        int totalTimeout = 0;

        try {
            for (TestId test : tests) {
                String selector = test.isClassOnly()
                        ? test.getClassName()
                        : test.getClassName() + "#" + test.getMethodName();
                TestNgResult result = runWithTimeout(
                        executor,
                        timeoutMs,
                        selector,
                        () -> execute(test)
                );
                if (result == null) {
                    totalTimeout += 1;
                    continue;
                }
                totalRun += result.run;
                totalFail += result.failed;
            }
        } finally {
            executor.shutdownNow();
        }

        System.out.println("[TestNgTestRunner] run=" + totalRun + " failed=" + totalFail
                + " timeout=" + totalTimeout);
        if (totalRun == 0 || totalFail > 0 || totalTimeout > 0) {
            throw new AssertionError(
                    "TestNG execution was not successful: run=" + totalRun
                            + " failed=" + totalFail + " timeout=" + totalTimeout
            );
        }
    }

    private TestNgResult execute(TestId test) throws ClassNotFoundException {
        Class<?> testClass = Class.forName(test.getClassName(), true, classLoader);
        TestListenerAdapter listener = new TestListenerAdapter();
        TestNG testng = new TestNG(false);
        testng.setUseDefaultListeners(false);
        testng.setTestClasses(new Class<?>[]{testClass});
        if (!test.isClassOnly()) {
            String methodName = test.getMethodName();
            testng.setMethodInterceptor((List<IMethodInstance> methods, ITestContext context) ->
                    methods.stream()
                            .filter(method -> method.getMethod().getMethodName().equals(methodName))
                            .toList()
            );
        }
        testng.addListener(listener);
        testng.run();

        int passed = listener.getPassedTests().size();
        int failed = listener.getFailedTests().size() + listener.getConfigurationFailures().size();
        int skipped = listener.getSkippedTests().size();
        for (ITestResult failure : listener.getFailedTests()) {
            Throwable cause = failure.getThrowable();
            System.out.println("[TestNgTestRunner] FAILURE in " + test.getClassName()
                    + "#" + failure.getMethod().getMethodName() + " :: "
                    + (cause == null ? "Unknown failure" : cause));
        }
        System.out.println("[TestNgTestRunner] passed=" + passed + " failed=" + failed
                + " skipped=" + skipped);
        return new TestNgResult(passed + failed + skipped, failed);
    }

    private TestNgResult runWithTimeout(ExecutorService executor,
                                        long timeoutMs,
                                        String selector,
                                        java.util.concurrent.Callable<TestNgResult> task) {
        Future<TestNgResult> future = executor.submit(task);
        try {
            return timeoutMs <= 0 ? future.get() : future.get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);
            System.out.println("[TestNgTestRunner] TIMEOUT after " + timeoutMs + "ms: " + selector);
            return null;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Test interrupted: " + selector, e);
        } catch (Exception e) {
            throw new RuntimeException("Test failed: " + selector, e);
        }
    }

    private record TestNgResult(int run, int failed) {}
}
