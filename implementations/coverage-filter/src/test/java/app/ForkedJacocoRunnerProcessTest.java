package app;

import org.jacoco.agent.rt.RT;
import org.junit.Test;

import java.io.File;
import java.nio.file.Files;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public class ForkedJacocoRunnerProcessTest {

    @Test
    public void allowingFailureReturnsNonzeroForkExit() throws Exception {
        File agentJar = new File(RT.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        assertTrue("Expected JaCoCo runtime agent jar: " + agentJar, agentJar.isFile());

        File emptyLibs = Files.createTempDirectory("forked-runner-libs-").toFile();
        File execFile = Files.createTempFile("forked-runner-", ".exec").toFile();
        String classPath = System.getProperty("java.class.path");
        ForkedJacocoRunner runner = new ForkedJacocoRunner(
                agentJar.getAbsolutePath(),
                emptyLibs,
                classPath,
                classPath,
                classPath,
                RunMany.class.getName()
        );

        int exit = runner.runSelectorsAllowingFailure(
                List.of(ExitTest.class.getName() + "#exitsWith55"),
                execFile,
                false
        );

        assertEquals(55, exit);
    }

    @Test
    public void forkUsesConfiguredUserDirectoryForRelativePaths() throws Exception {
        File agentJar = new File(RT.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        File emptyLibs = Files.createTempDirectory("forked-runner-libs-").toFile();
        File workingDirectory = Files.createTempDirectory("forked-runner-cwd-").toFile();
        assertTrue(new File(workingDirectory, "relative-marker.txt").createNewFile());
        File execFile = Files.createTempFile("forked-runner-", ".exec").toFile();
        String classPath = System.getProperty("java.class.path");
        ForkedJacocoRunner runner = new ForkedJacocoRunner(
                agentJar.getAbsolutePath(),
                emptyLibs,
                classPath,
                classPath,
                classPath,
                RunMany.class.getName()
        );

        String originalUserDir = System.getProperty("user.dir");
        try {
            System.setProperty("user.dir", workingDirectory.getAbsolutePath());
            runner.runSelectors(
                    List.of(WorkingDirectoryTest.class.getName() + "#seesRelativeMarker"),
                    execFile,
                    false
            );
        } finally {
            System.setProperty("user.dir", originalUserDir);
        }
    }

    public static class ExitTest {
        @Test
        public void exitsWith55() {
            System.exit(55);
        }
    }

    public static class WorkingDirectoryTest {
        @Test
        public void seesRelativeMarker() {
            assertTrue(new File("relative-marker.txt").isFile());
        }
    }
}
