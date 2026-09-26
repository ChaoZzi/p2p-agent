@echo off
REM ============================================================================
REM 启动 Java 侧 mock-oa（默认 :8000，sqlite profile）
REM   在【任意目录】都能跑：脚本自己算项目根，不依赖你当前在哪儿
REM   Maven 用全路径（git-bash 里的 mvn 包装器会报 classworlds 错误）
REM
REM 切 PostgreSQL：
REM   scripts\run-java.cmd -Dspring-boot.run.profiles=postgres
REM   （先用 psql 跑 scripts\pg-init.sql 建角色/库，密码放本地未提交的
REM     java-service\src\main\resources\application-postgres-local.properties）
REM ============================================================================
setlocal
set "ROOT=%~dp0.."
"D:\software\maven-dist\apache-maven-3.9.9\bin\mvn.cmd" -f "%ROOT%\java-service\pom.xml" spring-boot:run %*
