package app;

import junit.framework.TestCase;
import org.junit.Test;
import org.junit.runner.JUnitCore;
import org.junit.runner.Request;
import org.junit.runner.Result;

import java.util.List;

import static org.junit.Assert.assertEquals;

public class JUnit3DiscoveryTest {

    @Test
    public void discoversPublicZeroArgumentTestCaseMethodsWithoutAnnotations() {
        JUnit5MethodDiscoverer discoverer =
                new JUnit5MethodDiscoverer(getClass().getClassLoader());

        List<String> methods = discoverer.discoverTestMethods(LegacyStyleTest.class.getName());

        assertEquals(
                List.of("testAnnotated", "testByNamingConvention"),
                methods
        );
        assertEquals(TestDetector.JUnitVersion.JUNIT_4, TestDetector.detect(LegacyStyleTest.class));
    }

    @Test
    public void junit4RunnerCanExecuteASelectedJUnit3Method() {
        Result result = new JUnitCore().run(
                Request.method(LegacyStyleTest.class, "testByNamingConvention")
        );

        assertEquals(1, result.getRunCount());
        assertEquals(0, result.getFailureCount());
    }

    public static class LegacyStyleTest extends TestCase {
        public void testByNamingConvention() {
            assertTrue(true);
        }

        @Test
        public void testAnnotated() {
            assertTrue(true);
        }

        public void helper() {
        }

        public int testWrongReturnType() {
            return 1;
        }

        public void testWithParameter(String ignored) {
        }

        private void testPrivate() {
        }
    }
}
