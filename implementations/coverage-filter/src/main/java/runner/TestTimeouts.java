package runner;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

public final class TestTimeouts {

    public static final String TIMEOUT_PROP = "test.timeout.ms";
    public static final String TIMEOUT_ENV = "TEST_TIMEOUT_MS";
    public static final long DEFAULT_TIMEOUT_MS = TimeUnit.MINUTES.toMillis(2);

    private TestTimeouts() {
    }

    public static long resolveTimeoutMs() {
        String val = System.getProperty(TIMEOUT_PROP);
        if (val == null || val.isBlank()) {
            val = System.getenv(TIMEOUT_ENV);
        }
        if (val == null || val.isBlank()) {
            return DEFAULT_TIMEOUT_MS;
        }
        try {
            long parsed = Long.parseLong(val.trim());
            return Math.max(parsed, 0);
        } catch (NumberFormatException e) {
            return DEFAULT_TIMEOUT_MS;
        }
    }

    public static ExecutorService newExecutor(String namePrefix) {
        ThreadFactory tf = r -> {
            Thread t = new Thread(r);
            t.setDaemon(true);
            t.setName(namePrefix + "-" + t.getId());
            return t;
        };
        return Executors.newSingleThreadExecutor(tf);
    }
}
