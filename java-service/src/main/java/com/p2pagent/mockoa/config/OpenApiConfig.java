package com.p2pagent.mockoa.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.servers.Server;
import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * OpenAPI 元信息。
 *
 * <p>不配这个的话，导出的 docs/contracts/java-service-openapi.json 里
 * info.title 会是默认的 "OpenAPI definition"、version 是 "v0"——一份要交给
 * Python 侧当契约用的文件，标题和版本都没有就等于没写。这里硬编码是刻意的：
 * 契约版本应与 P0 任务卡挂钩，由代码显式声明而不是跟着 pom 的 SNAPSHOT 漂。
 */
@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI mockOaOpenApi() {
        return new OpenAPI()
                .info(new Info()
                        .title("mock-oa API")
                        .version("0.1.0-P0-01")
                        .description("P2P Agent P0-01：mock OA 系统（trace 透传 / request_id 幂等 / 故障注入开关）"))
                .servers(List.of(new Server().url("http://127.0.0.1:8000").description("本地开发")));
    }
}
