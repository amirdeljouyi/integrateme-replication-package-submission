package model;

import java.util.Objects;

public final class TestId {
    private final String className;
    private final String methodName; // null means "run whole class"

    public TestId(String className, String methodName) {
        this.className = Objects.requireNonNull(className, "className");
        this.methodName = methodName; // may be null
    }

    public String getClassName() { return className; }
    public String getMethodName() { return methodName; }
    public boolean isClassOnly() { return methodName == null || methodName.isBlank(); }

    public static TestId fromString(String s) {
        String trimmed = s.trim();
        if (trimmed.isEmpty()) throw new IllegalArgumentException("Empty test id line");

        int idx = trimmed.lastIndexOf('#');
        if (idx < 0) {
            // class-only entry
            return new TestId(trimmed, null);
        }
        if (idx == 0 || idx == trimmed.length() - 1) {
            throw new IllegalArgumentException("Expected format fqcn or fqcn#methodName, got: " + s);
        }
        String cn = trimmed.substring(0, idx).trim();
        String mn = trimmed.substring(idx + 1).trim();
        return new TestId(cn, mn);
    }

    @Override public String toString() {
        return isClassOnly() ? className : (className + "#" + methodName);
    }
}