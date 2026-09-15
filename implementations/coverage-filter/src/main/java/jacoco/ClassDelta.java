package jacoco;

public final class ClassDelta {
    private final String className;
    private final int addedLines;
    private final int addedInstructions;
    private final int addedBranches;
    private final int addedMethods;

    public ClassDelta(String className, int addedLines, int addedInstructions, int addedBranches, int addedMethods) {
        this.className = className;
        this.addedLines = addedLines;
        this.addedInstructions = addedInstructions;
        this.addedBranches = addedBranches;
        this.addedMethods = addedMethods;
    }

    public String getClassName() { return className; }
    public int getAddedLines() { return addedLines; }
    public int getAddedInstructions() { return addedInstructions; }
    public int getAddedBranches() { return addedBranches; }
    public int getAddedMethods() { return addedMethods; }
}