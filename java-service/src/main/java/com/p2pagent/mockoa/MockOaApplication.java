package com.p2pagent.mockoa;

import java.nio.file.Files;
import java.nio.file.Path;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * P0-01 mock-oa 冒烟服务入口。
 *
 * <p>启动时先把 SQLite 所在目录建出来：sqlite-jdbc 会创建 .db 文件，但不会创建父目录，
 * 目录不存在会直接以 "path to ... does not exist" 起不来。
 */
@SpringBootApplication
public class MockOaApplication {

    private static final Logger log = LoggerFactory.getLogger(MockOaApplication.class);

    private static final String DEFAULT_DB_PATH = "data/mock-oa.db";

    public static void main(String[] args) throws Exception {
        String dbPath = System.getenv("APP_DB_PATH");
        if (dbPath == null || dbPath.isBlank()) {
            dbPath = DEFAULT_DB_PATH;
        }
        Path dbFile = Path.of(dbPath).toAbsolutePath().normalize();
        Files.createDirectories(dbFile.getParent());
        log.info("mock-oa starting: sqlite={} workdir={}", dbFile, Path.of("").toAbsolutePath());
        SpringApplication.run(MockOaApplication.class, args);
    }
}
