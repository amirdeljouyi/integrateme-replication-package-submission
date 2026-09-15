package model;

import java.util.Objects;

public final class LineDeltaRow {
    private final String testSelector;
    private final String className;
    private final String newlyCoveredRanges;
    private final String upgradedToFullRanges;

    public LineDeltaRow(String testSelector,
                        String className,
                        String newlyCoveredRanges,
                        String upgradedToFullRanges) {
        this.testSelector = Objects.requireNonNull(testSelector, "testSelector");
        this.className = Objects.requireNonNull(className, "className");
        this.newlyCoveredRanges = newlyCoveredRanges == null ? "" : newlyCoveredRanges;
        this.upgradedToFullRanges = upgradedToFullRanges == null ? "" : upgradedToFullRanges;
    }

    public String getTestSelector() { return testSelector; }
    public String getClassName() { return className; }
    public String getNewlyCoveredRanges() { return newlyCoveredRanges; }
    public String getUpgradedToFullRanges() { return upgradedToFullRanges; }
}