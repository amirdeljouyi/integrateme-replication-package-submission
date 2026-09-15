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

public final class ListJUnit5Tests {

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: ListJUnit5Tests <testClassFqcn>");
        }

        String fqcn = args[0];
        ClassLoader cl = Thread.currentThread().getContextClassLoader();
        Class<?> c = Class.forName(fqcn, true, cl);

        List<String> methods = new ArrayList<>();
        for (Method m : c.getDeclaredMethods()) {
            if (m.isAnnotationPresent(Test.class)
                    || m.isAnnotationPresent(RepeatedTest.class)
                    || m.isAnnotationPresent(ParameterizedTest.class)
                    || m.isAnnotationPresent(TestFactory.class)
                    || m.isAnnotationPresent(TestTemplate.class)) {
                methods.add(m.getName());
            }
        }

        methods.sort(Comparator.naturalOrder());
        for (String name : methods) {
            System.out.println(name);
        }
    }
}