@echo off
REM ===========================================================================
REM Start Java mock-oa on :8000 (sqlite profile by default).
REM Works from ANY directory: the script resolves the project root itself.
REM Maven is called by absolute path (the git-bash "mvn" wrapper fails with a
REM classworlds ClassNotFoundException - it is a MSYS wrapper issue, not Maven).
REM
REM PostgreSQL profile:
REM   scripts\run-java.cmd -Dspring-boot.run.profiles=postgres
REM   (prerequisite: run scripts\pg-init.cmd once, and put the password into
REM    local, gitignored application-postgres-local.properties)
REM ===========================================================================
setlocal
set "ROOT=%~dp0.."
"D:\software\maven-dist\apache-maven-3.9.9\bin\mvn.cmd" -f "%ROOT%\java-service\pom.xml" spring-boot:run %*
