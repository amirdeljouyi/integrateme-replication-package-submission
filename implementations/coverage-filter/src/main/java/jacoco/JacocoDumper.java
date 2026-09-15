package jacoco;

import org.jacoco.core.tools.ExecFileLoader;
import org.jacoco.core.tools.ExecDumpClient;

import java.io.File;
import java.io.IOException;
import java.net.InetAddress;
import java.util.Objects;

public final class JacocoDumper {

    private final InetAddress address;
    private final int port;

    public JacocoDumper(InetAddress address, int port) {
        this.address = Objects.requireNonNull(address, "address");
        this.port = port;
    }

    public void dumpTo(File execOut, boolean reset) throws IOException {
        Objects.requireNonNull(execOut, "execOut");

        ExecDumpClient client = new ExecDumpClient();
        ExecFileLoader loader = new ExecFileLoader();

        client.setDump(true);
        client.setReset(reset);
        client.dump(address, port);

        loader.save(execOut, false);

        System.out.println("[CoverageFilterApp] wrote " + execOut.getAbsolutePath()
                + " size=" + execOut.length() + " bytes");
    }
}