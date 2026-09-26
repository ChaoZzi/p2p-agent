<#
P0-02 PG 侧库内校验（只读 + 两条权限探针，不改数据）。
用法：
  pwsh -NoProfile -File scripts/pg-checks.ps1
  pwsh -NoProfile -File scripts/pg-checks.ps1 -RequestId req-reg-xxxx -ConcurrentRequestId req-reg-conc-xxxx

要点：
  - 密码只从「本地未提交」的 application-postgres-local.properties 读取，不打印、不落文件；
  - append-only 探针写在事务里并 ROLLBACK，不留脏数据。
#>
param(
  [string]$RequestId = "",
  [string]$ConcurrentRequestId = "",
  [string]$DbName = "p2p_agent",
  [string]$DbUser = "p2p_agent",
  [string]$DbHost = "127.0.0.1",
  [int]$DbPort = 5432
)

$root = Join-Path $PSScriptRoot ".."
$localFile = Join-Path $root "java-service\src\main\resources\application-postgres-local.properties"
if (-not (Test-Path $localFile)) {
  Write-Output ("[X] local credential file not found: " + $localFile)
  Write-Output "    create it with: spring.datasource.password=<your pg password>  (it is gitignored)"
  exit 2
}
$pw = (Select-String -Path $localFile -Pattern '^spring\.datasource\.password=(.*)$').Matches[0].Groups[1].Value.Trim()
$env:PGPASSWORD = $pw

$psql = "D:\software\postgres\bin\psql.exe"
if (-not (Test-Path $psql)) {
  Write-Output ("[X] psql not found: " + $psql)
  exit 2
}
$base = @("-U", $DbUser, "-h", $DbHost, "-p", "$DbPort", "-d", $DbName)

function Q([string]$title, [string]$sql) {
  Write-Output ""
  Write-Output ("### " + $title)
  & $psql @base -c $sql
}

Q "库/角色" "select current_user, current_database(), version();"
Q "三张表是否存在" "select table_name from information_schema.tables where table_schema='public' order by table_name;"
Q "\d idempotency" "\d idempotency"
Q "\d flow" "\d flow"
Q "\d audit_log" "\d audit_log"
Q "audit_log 上 p2p_agent 的实际权限（append-only 的数据库层证据）" "select grantee, privilege_type from information_schema.role_table_grants where table_name='audit_log' and grantee='p2p_agent' order by privilege_type;"

Write-Output ""
Write-Output "### append-only 探针 1/3：INSERT 允许（事务内插入后回滚，不留数据）"
& $psql @base -c "begin; insert into audit_log(audit_id, flow_id, actor, action, detail, trace_id, at) values ('probe-insert-check','probe-flow','probe','PROBE','permission probe','probe-trace','1970-01-01T00:00:00Z'); select count(*) as probe_rows_visible_in_tx from audit_log where audit_id='probe-insert-check'; rollback;"
Write-Output ""
Write-Output "### append-only 探针 2/3：UPDATE 必须被拒绝（期望 ERROR: permission denied）"
& $psql @base -c "update audit_log set actor='attacker' where false;"
Write-Output ""
Write-Output "### append-only 探针 3/3：DELETE 必须被拒绝（期望 ERROR: permission denied）"
& $psql @base -c "delete from audit_log where false;"

Q "各表行数" "select 'idempotency' as t, count(*) from idempotency union all select 'flow', count(*) from flow union all select 'audit_log', count(*) from audit_log order by t;"

if ($RequestId -ne "") {
  Q ("指定 request_id 的行数（应为 1）：" + $RequestId) ("select request_id, count(*) as rows from idempotency where request_id = '" + $RequestId + "' group by request_id;")
}
if ($ConcurrentRequestId -ne "") {
  Q ("5 并发那个 request_id 的行数（应为 1）：" + $ConcurrentRequestId) ("select request_id, count(*) as rows from idempotency where request_id = '" + $ConcurrentRequestId + "' group by request_id;")
}
Write-Output ""
Write-Output "pg-checks done."
