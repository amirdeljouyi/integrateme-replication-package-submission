package jacoco;

public final class TestDelta {
    private final String testSelector;
    private final int addedLines;
    private final int addedMethods;
    private final int addedBranches;
    private final int addedInstructions;

    public TestDelta(String testSelector, int addedLines, int addedMethods, int addedBranches, int addedInstructions) {
        this.testSelector = testSelector;
        this.addedLines = addedLines;
        this.addedMethods = addedMethods;
        this.addedBranches = addedBranches;
        this.addedInstructions = addedInstructions;
    }

    public String getTestSelector() { return testSelector; }
    public int getAddedLines() { return addedLines; }
    public int getAddedMethods() { return addedMethods; }
    public int getAddedBranches() { return addedBranches; }
    public int getAddedInstructions() { return addedInstructions; }
}