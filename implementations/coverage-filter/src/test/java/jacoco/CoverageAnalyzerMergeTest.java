package jacoco;

import org.jacoco.core.data.ExecutionData;
import org.jacoco.core.tools.ExecFileLoader;
import org.junit.Test;

import java.io.File;
import java.nio.file.Files;

import static org.junit.Assert.assertArrayEquals;

public class CoverageAnalyzerMergeTest {

    @Test
    public void mergeExecsPreservesBaselineAndAddsRetainedSuiteProbes() throws Exception {
        long classId = 42L;
        File baseline = writeExec(classId, new boolean[]{true, false, false});
        File retainedSuite = writeExec(classId, new boolean[]{false, true, false});
        File destination = Files.createTempFile("merged-coverage-", ".exec").toFile();

        CoverageAnalyzer analyzer = new CoverageAnalyzer(
                Files.createTempDirectory("empty-classes-").toFile()
        );
        analyzer.mergeExecs(destination, baseline, retainedSuite);

        ExecFileLoader merged = new ExecFileLoader();
        merged.load(destination);
        assertArrayEquals(
                new boolean[]{true, true, false},
                merged.getExecutionDataStore().get(classId).getProbes()
        );
    }

    private File writeExec(long classId, boolean[] probes) throws Exception {
        File file = Files.createTempFile("coverage-", ".exec").toFile();
        ExecFileLoader loader = new ExecFileLoader();
        loader.getExecutionDataStore().put(new ExecutionData(classId, "example/Target", probes));
        loader.save(file, false);
        return file;
    }
}
