package app;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class ListTests {

    public static final String METHOD_PREFIX = "COVFILTER_TEST_METHOD\t";

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: ListTests <testClassFqcn>");
        }

        String fqcn = args[0];
        ClassLoader cl = Thread.currentThread().getContextClassLoader();
        Class<?> c = Class.forName(fqcn, true, cl);

        TestDetector.JUnitVersion version = TestDetector.detect(c);
        
        List<String> methods = new ArrayList<>();
        if (version == TestDetector.JUnitVersion.JUNIT_5) {
            methods = listJUnit5Tests(c);
            if (methods.isEmpty()) {
                methods = listTestNgTests(c);
            }
        } else if (version == TestDetector.JUnitVersion.JUNIT_4) {
            methods = listJUnit4Tests(c);
        } else if (version == TestDetector.JUnitVersion.TESTNG) {
            methods = listTestNgTests(c);
        } else {
            // Default to trying to list both if unknown
            methods = listJUnit5Tests(c);
            if (methods.isEmpty()) {
                methods = listJUnit4Tests(c);
            }
        }

        methods.sort(Comparator.naturalOrder());
        for (String name : methods) {
            System.out.println(METHOD_PREFIX + name);
        }
    }

    private static List<String> listJUnit5Tests(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (isJUnit5TestMethod(m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private static List<String> listJUnit4Tests(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (TestDetector.isJUnit4TestMethod(c, m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private static List<String> listTestNgTests(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (TestDetector.isTestNgTestMethod(m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private static boolean isJUnit5TestMethod(Method m) {
        try {
            return m.isAnnotationPresent(org.junit.jupiter.api.Test.class)
                    || m.isAnnotationPresent(org.junit.jupiter.api.RepeatedTest.class)
                    || m.isAnnotationPresent(org.junit.jupiter.params.ParameterizedTest.class)
                    || m.isAnnotationPresent(org.junit.jupiter.api.TestFactory.class)
                    || m.isAnnotationPresent(org.junit.jupiter.api.TestTemplate.class);
        } catch (NoClassDefFoundError e) {
            return false;
        }
    }

}
