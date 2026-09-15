package app;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.TestTemplate;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;

public final class JUnit5MethodDiscoverer {

    private final ClassLoader classLoader;

    public JUnit5MethodDiscoverer(ClassLoader classLoader) {
        this.classLoader = Objects.requireNonNull(classLoader, "classLoader");
    }

    public List<String> discoverTestMethods(String testClassFqcn) {
        Class<?> c = load(testClassFqcn);

        TestDetector.JUnitVersion version = TestDetector.detect(c);

        List<String> methods = new ArrayList<>();
        if (version == TestDetector.JUnitVersion.JUNIT_5) {
            methods = discoverJUnit5TestMethods(c);
            if (methods.isEmpty()) {
                methods = discoverTestNgTestMethods(c);
            }
        } else if (version == TestDetector.JUnitVersion.JUNIT_4) {
            methods = discoverJUnit4TestMethods(c);
        } else if (version == TestDetector.JUnitVersion.TESTNG) {
            methods = discoverTestNgTestMethods(c);
        } else {
            methods = discoverJUnit5TestMethods(c);
            if (methods.isEmpty()) {
                methods = discoverJUnit4TestMethods(c);
            }
        }

        methods.sort(Comparator.naturalOrder());
        return methods;
    }

    private List<String> discoverJUnit5TestMethods(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (isJUnit5TestMethod(m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private List<String> discoverJUnit4TestMethods(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (TestDetector.isJUnit4TestMethod(c, m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private List<String> discoverTestNgTestMethods(Class<?> c) {
        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (TestDetector.isTestNgTestMethod(m)) {
                methods.add(m.getName());
            }
        }
        return methods;
    }

    private boolean isJUnit5TestMethod(Method m) {
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

    private Class<?> load(String fqcn) {
        try {
            return Class.forName(fqcn, true, classLoader);
        } catch (ClassNotFoundException e) {
            throw new RuntimeException("Cannot load test class: " + fqcn, e);
        }
    }
}
