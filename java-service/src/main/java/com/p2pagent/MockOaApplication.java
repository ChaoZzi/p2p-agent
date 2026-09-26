package com.p2pagent;

import java.nio.file.Files;
import java.nio.file.Path;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.event.ApplicationEnvironmentPreparedEvent;
import org.springframework.context.ApplicationListener;
import org.springframework.core.env.ConfigurableEnvironment;

/**
 * 启动类（P0-02 §A4 从 {@code com.p2pagent.mockoa} 上移到 {@code com.p2pagent}）。
 *
 * <p><b>为什么必须上移</b>：{@code @SpringBootApplication} 只扫描<b>它自己所在包及其子包</b>。
 * 之前类在 {@code com.p2pagent.mockoa}，于是 {@code com.p2pagent.engine} / {@code .db} /
 * {@code .web} 这些新包里的 {@code @Service}/{@code @Repository}/{@code @RestController}
 * 根本不会被扫到 —— 症状是启动时报 "no qualifying bean"（而不是编译错误），
 * 排查起来会绕远路。放在 {@code com.p2pagent} 后，mockoa 与新包都在扫描范围内。
 */
@SpringBootApplication
public class MockOaApplication {

    private static final Logger log = LoggerFactory.getLogger(MockOaApplication.class);

    private static final String SQLITE_URL_PREFIX = "jdbc:sqlite:";

    public static void main(String[] args) {
        SpringApplication application = new SpringApplication(MockOaApplication.class);
        // 显式写成具体事件类型的监听器：直接传方法引用时，编译器会推断成
        // ApplicationListener<ApplicationEvent>，与 addListeners 的参数类型对不上
        ApplicationListener<ApplicationEnvironmentPreparedEvent> prepareSqliteDir =
                MockOaApplication::prepareSqliteDirectoryIfNeeded;
        application.addListeners(prepareSqliteDir);
        application.run(args);
    }

    /**
     * 只有 SQLite 才需要预建库文件所在目录（sqlite-jdbc 会创建 .db 文件，但<b>不会</b>创建父目录，
     * 目录不存在会直接启动失败）。PG 不需要任何本地目录 —— 早期版本无条件建 {@code data/}，
     * 在 PG profile 下会留下一个空目录（无害但脏），P0-02 §A4 改掉。
     *
     * <p><b>为什么用 ApplicationEnvironmentPreparedEvent，而不是 @PostConstruct/@Bean</b>：
     * 建目录必须发生在 DataSource 初始化之前；这个事件在 environment 就绪（配置数据、profile 都已解析）
     * 之后、context refresh 之前发布，是唯一"够早且拿得到配置"的时机。
     *
     * <p><b>为什么不判断 profile 名字</b>：直接看解析后的 JDBC URL 前缀。
     * 它是配置的最终结果（profile 默认值、命令行覆盖、APP_DB_PATH 全都在里面），
     * 比"猜当前是不是 sqlite profile"更接近事实，也顺带成为 db 路径的唯一真相来源。
     */
    static void prepareSqliteDirectoryIfNeeded(ApplicationEnvironmentPreparedEvent event) {
        ConfigurableEnvironment environment = event.getEnvironment();
        String url = environment.getProperty("spring.datasource.url", "");
        if (!url.startsWith(SQLITE_URL_PREFIX)) {
            log.debug("datasource is not sqlite (url starts with neither), skip directory creation");
            return;
        }
        String rawPath = url.substring(SQLITE_URL_PREFIX.length());
        int queryIndex = rawPath.indexOf('?');
        if (queryIndex >= 0) {
            rawPath = rawPath.substring(0, queryIndex);
        }
        if (rawPath.isBlank() || rawPath.startsWith(":memory:")) {
            return;
        }
        Path dbFile = Path.of(rawPath).toAbsolutePath().normalize();
        Path parent = dbFile.getParent();
        if (parent == null) {
            return;
        }
        try {
            Files.createDirectories(parent);
        } catch (Exception e) {
            throw new IllegalStateException("cannot create directory for sqlite database: " + parent, e);
        }
        log.info("sqlite profile: ensured database directory {} (db file {})", parent, dbFile);
    }
}
