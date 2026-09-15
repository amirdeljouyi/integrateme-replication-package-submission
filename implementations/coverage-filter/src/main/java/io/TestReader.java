package io;

import model.TestId;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.nio.file.Files;

public class TestReader {
    File mwtFile;
    File agtFile;

    public TestReader(String mwtFile, String agtFile) {
        this.mwtFile= new File(mwtFile);
        this.agtFile= new File(agtFile);
    }

    private Path path(File f){
        return f.toPath();
    }

    public List<TestId> readMWTTests() throws IOException {
        return read(mwtFile);
    }

    public List<TestId> readAGTTests() throws IOException {
        return read(agtFile);
    }

    public List<TestId> read(File file) throws IOException {
        List<String> lines = Files.readAllLines(file.toPath());
        List<TestId> out = new ArrayList<>();

        for (String line : lines) {
            String s = line.trim();
            if (s.isEmpty() || s.startsWith("#")) continue;
            out.add(TestId.fromString(s));
        }

        return out;
    }
}
