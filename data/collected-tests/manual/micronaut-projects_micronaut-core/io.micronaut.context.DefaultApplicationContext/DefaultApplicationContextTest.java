package io.micronaut.context;

import spock.lang.Issue;

import static org.junit.jupiter.api.Assertions.assertFalse;

public class DefaultApplicationContextTest {
    @Issue("https://github.com/micronaut-projects/micronaut-test/issues/615#issuecomment-1516355815")
    @Test
    public void applicationContextShouldShutDownTheEnvironmentItCreated() throws Exception {
        ApplicationContext ctx = ApplicationContext.builder().build();
        ctx.start();
        Environment env = ctx.getEnvironment();
        assertTrue(env.isRunning());
        ctx.stop();
        assertFalse(env.isRunning(), "expected to be stopped");
        assertFalse(ctx.isRunning(), "expected to be stopped");
    }

    @Test
    public void applicationContextShouldNotStopTheEnvironmentItDidNotCreate() throws Exception {
        ApplicationContext ctx = ApplicationContext.builder().build();
        ctx.start();

        // providing ctx with an external environment
        ApplicationContext ctx2 = ApplicationContext.create(ctx.getEnvironment());
        Assertions.assertEquals(ctx.getEnvironment(), ctx2.getEnvironment());
        ctx2.start();
        ctx2.stop();

        assertTrue(ctx.getEnvironment().isRunning(), "shouldn't stop an external environment");

        ctx.stop();
        assertFalse(ctx.isRunning(), "expected to be stopped");
        assertFalse(ctx.getEnvironment().isRunning());
    }
}
