package model;

import java.util.Objects;

/** Coverage key at method granularity (class + method signature-ish). */
public final class MethodKey {
    public final String className;
    public final String methodName;

    public MethodKey(String className, String methodName) {
        this.className = className;
        this.methodName = methodName;
    }

    @Override public boolean equals(Object o) {
        if (!(o instanceof MethodKey)) return false;
        MethodKey m = (MethodKey) o;
        return Objects.equals(className, m.className) && Objects.equals(methodName, m.methodName);
    }

    @Override public int hashCode() { return Objects.hash(className, methodName); }

    @Override public String toString() { return className + "::" + methodName; }
}