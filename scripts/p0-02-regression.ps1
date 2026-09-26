<#
P0-02 回归套件：21 项断言，对任意 profile / 端口跑同一份。
用法（任选）：
  scripts\run-regression.cmd                                   # 默认 sqlite、:8000
  scripts\run-regression.cmd -Label postgres                   # postgres profile 实例
  pwsh -NoProfile -File scripts/p0-02-regression.ps1 -BaseUrl http://127.0.0.1:8000 -Label sqlite
退出码 0 = 21 项全过；非 0 = 有用例失败。

编码说明（重要，别再踩）：
  Windows 下把「含中文的 JSON」当参数传给 curl.exe 时，PowerShell 会按 ANSI(GBK) 转换参数，
  Java 侧收到非法 UTF-8（实证日志：malformed json body: Invalid UTF-8 start byte 0xbd）。
  因此本套件：
    - 常规用例的 payload 一律 ASCII（applicant=tester）；
    - 第 5 项专门用「UTF-8 文件 + curl -d @file」跑一次中文 payload，把跨语言 UTF-8 也验掉 —— 且不经过命令行参数编码。
#>
param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [string]$Label = "sqlite",
  [string]$Work = (Join-Path $env:TEMP "p0-02-regression")
)
$script:pass = 0
$script:fail = 0
New-Item -ItemType Directory -Force -Path $Work | Out-Null

function Check([string]$name, [bool]$ok, [string]$detail) {
  if ($ok) { $script:pass++ } else { $script:fail++ }
  if ($ok) { $tag = "[PASS]" } else { $tag = "[FAIL]" }
  $line = $tag + " " + $name
  if ($detail) { $line = $line + "  | " + $detail }
  Write-Output $line
}

function Call([string]$method, [string]$path, [hashtable]$headers, [string]$body, [string]$bodyFile) {
  $hf = Join-Path $Work ("h_" + [guid]::NewGuid().ToString("N") + ".txt")
  $bf = Join-Path $Work ("b_" + [guid]::NewGuid().ToString("N") + ".txt")
  $cargs = @("-s", "--noproxy", "*", "-X", $method, "-D", $hf, "-o", $bf, "-w", "%{http_code}")
  if ($headers) { foreach ($k in $headers.Keys) { $cargs += @("-H", ($k + ": " + $headers[$k])) } }
  if ($body) { $cargs += @("-H", "Content-Type: application/json", "-d", $body) }
  if ($bodyFile) { $cargs += @("-H", "Content-Type: application/json", "-d", ("@" + $bodyFile)) }
  $cargs += ($BaseUrl + $path)
  $code = & curl.exe @cargs
  $ht = if (Test-Path $hf) { Get-Content -Raw $hf } else { "" }
  $bt = if (Test-Path $bf) { Get-Content -Raw $bf } else { "" }
  Remove-Item $hf, $bf -Force -ErrorAction SilentlyContinue
  $h = @{}
  foreach ($line in ($ht -split "\r?\n")) {
    if ($line -match "^([A-Za-z][A-Za-z0-9\-]*):\s*(.*)$") { $h[$matches[1]] = $matches[2].Trim() }
  }
  $j = $null
  try { $j = $bt | ConvertFrom-Json } catch { }
  return [pscustomobject]@{ Status = [int]$code; Headers = $h; Body = ($bt.Trim()); Json = $j }
}

function IsUuid([string]$s) { return ($s -match "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$") }

$suffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
$rid = "req-reg-$suffix"
$trace = "11111111-2222-4333-8444-555555555555"

Write-Output "=================================================================="
Write-Output ("P0-02 regression suite   profile=" + $Label + "   base=" + $BaseUrl)
Write-Output ("request_id basis = " + $rid + "   work=" + $Work)
Write-Output "=================================================================="

# ---- 1~4 trace 透传与兜底 ----
$r = Call "GET" "/healthz" @{ "X-Trace-Id" = $trace } $null $null
Check "1  healthz 200 + body status=ok" (($r.Status -eq 200) -and ($r.Json.status -eq "ok")) ("status=" + $r.Status + " body=" + $r.Body)
Check "2  healthz echo X-Trace-Id == sent" ($r.Headers["X-Trace-Id"] -eq $trace) ("echoed=" + $r.Headers["X-Trace-Id"])

$r = Call "GET" "/healthz" @{} $null $null
Check "3  no trace header -> java generates valid uuid" (IsUuid $r.Headers["X-Trace-Id"]) ("generated=" + $r.Headers["X-Trace-Id"])

$r = Call "GET" "/healthz" @{ "X-Trace-Id" = "not-a-uuid" } $null $null
Check "4  illegal trace -> fallback valid uuid (!= sent)" ((IsUuid $r.Headers["X-Trace-Id"]) -and ($r.Headers["X-Trace-Id"] -ne "not-a-uuid")) ("echoed=" + $r.Headers["X-Trace-Id"])

# ---- 5~10 幂等双调（第 5 项同时验证中文 UTF-8 payload 不被命令行编码破坏） ----
$utf8BodyFile = Join-Path $Work ("body_utf8_" + $suffix + ".json")
$utf8Json = '{"request_id":"' + $rid + '","flow_id":"flow-reg-' + $suffix + '","title":"MacBook Pro x1","amount":20000,"applicant":"姜盛超"}'
Set-Content -Path $utf8BodyFile -Value $utf8Json -Encoding utf8 -NoNewline
$b1 = '{"request_id":"' + $rid + '","flow_id":"flow-reg-' + $suffix + '","title":"MacBook Pro x1","amount":20000,"applicant":"tester"}'
$a1 = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid } $null $utf8BodyFile
Check "5  first create 200 + dedup=false (UTF-8 file payload, applicant=姜盛超)" (($a1.Status -eq 200) -and ($a1.Json.dedup -eq $false)) ("status=" + $a1.Status + " body=" + $a1.Body)
Check "6  first create header X-Dedup: false" ($a1.Headers["X-Dedup"] -eq "false") ("X-Dedup=" + $a1.Headers["X-Dedup"])

$a2 = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid } $b1 $null
Check "7  repeat create body.dedup=true" ($a2.Json.dedup -eq $true) ("body=" + $a2.Body)
Check "8  repeat create header X-Dedup: true" ($a2.Headers["X-Dedup"] -eq "true") ("X-Dedup=" + $a2.Headers["X-Dedup"])
Check "9  repeat create approval_id == first" ($a2.Json.approval_id -eq $a1.Json.approval_id) ("first=" + $a1.Json.approval_id + " second=" + $a2.Json.approval_id)
Check "10 repeat create created_at == first" ($a2.Json.created_at -eq $a1.Json.created_at) ("first=" + $a1.Json.created_at + " second=" + $a2.Json.created_at)

# ---- 11~16 422 四类 + 负值 + 错误体带 trace ----
$r = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace } $b1 $null
Check "11 422 missing X-Request-Id: code=VALIDATION" (($r.Status -eq 422) -and ($r.Json.error.code -eq "VALIDATION")) ("status=" + $r.Status + " body=" + $r.Body)

$badBody = '{"request_id":"req-other-' + $suffix + '","flow_id":"f","title":"t","amount":1,"applicant":"tester"}'
$r = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid } $badBody $null
Check "12 422 header != body.request_id" (($r.Status -eq 422) -and ($r.Json.error.message -eq "X-Request-Id header must equal body.request_id")) ("status=" + $r.Status + " msg=" + $r.Json.error.message)

$blankBody = '{"request_id":"  ","flow_id":"f","title":"t","amount":1,"applicant":"tester"}'
$r = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = "  " } $blankBody $null
Check "13 422 blank body.request_id" (($r.Status -eq 422) -and ($r.Json.error.message -eq "request_id is required")) ("status=" + $r.Status + " msg=" + $r.Json.error.message)

$noFlow = '{"request_id":"' + $rid + '","title":"t","amount":1,"applicant":"tester"}'
$r = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid } $noFlow $null
Check "14 422 missing flow_id" (($r.Status -eq 422) -and ($r.Json.error.message -eq "flow_id is required")) ("status=" + $r.Status + " msg=" + $r.Json.error.message)

$negAmount = '{"request_id":"' + $rid + '","flow_id":"f","title":"t","amount":-1,"applicant":"tester"}'
$r = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid } $negAmount $null
Check "15 422 negative amount" (($r.Status -eq 422) -and ($r.Json.error.message -eq "amount must be a non-negative number")) ("status=" + $r.Status + " msg=" + $r.Json.error.message)
Check "16 error body trace_id == request trace" ($r.Json.error.trace_id -eq $trace) ("error.trace_id=" + $r.Json.error.trace_id)

# ---- 17~20 故障开关 ----
$r = Call "GET" "/api/mock/faults" @{} $null $null
Check "17 fault switch initial timeout_ms=0 random_delay=false" (($r.Status -eq 200) -and ($r.Json.timeout_ms -eq 0) -and ($r.Json.random_delay -eq $false)) ("body=" + $r.Body)

$null = Call "PUT" "/api/mock/faults" @{} '{"timeout_ms":3000,"random_delay":false}' $null
$rid2 = "req-reg-slow-$suffix"
$b2 = '{"request_id":"' + $rid2 + '","flow_id":"flow-slow-' + $suffix + '","title":"slow probe","amount":1000,"applicant":"tester"}'
$sw = Measure-Command { $null = Call "POST" "/api/mock/oa/approvals" @{ "X-Trace-Id" = $trace; "X-Request-Id" = $rid2 } $b2 $null }
$elapsed = [math]::Round($sw.TotalSeconds, 3)
Check "18 fault delay effective: elapsed >= 3s" ($elapsed -ge 3.0) ("elapsed_seconds=" + $elapsed)

$r = Call "PUT" "/api/mock/faults" @{} '{"timeout_ms":99999,"random_delay":false}' $null
$clamped = ($r.Json.timeout_ms -eq 10000)
$neg = Call "PUT" "/api/mock/faults" @{} '{"timeout_ms":-5,"random_delay":false}' $null
Check "19 over-limit clamped to 10000 and negative -> 422" ($clamped -and ($neg.Status -eq 422)) ("clamped=" + $r.Json.timeout_ms + " negative_status=" + $neg.Status + " msg=" + $neg.Json.error.message)

$null = Call "PUT" "/api/mock/faults" @{} '{"timeout_ms":0,"random_delay":false}' $null
$keep = Call "PUT" "/api/mock/faults" @{} '{"random_delay":true}' $null
$r = Call "GET" "/api/mock/faults" @{} $null $null
Check "20 null field keeps previous value" (($keep.Json.timeout_ms -eq 0) -and ($keep.Json.random_delay -eq $true)) ("PUT resp=" + $keep.Body + " GET=" + $r.Body)
$null = Call "PUT" "/api/mock/faults" @{} '{"timeout_ms":0,"random_delay":false}' $null

# ---- 21 5 并发同 request_id ----
$rid3 = "req-reg-conc-$suffix"
$b3 = '{"request_id":"' + $rid3 + '","flow_id":"flow-conc-' + $suffix + '","title":"concurrent","amount":3000,"applicant":"tester"}'
$conc = 1..5 | ForEach-Object -Parallel {
  $bf = Join-Path $using:Work ("cb_" + [guid]::NewGuid().ToString("N") + ".txt")
  $null = & curl.exe -s --noproxy "*" -X POST -o $bf ($using:BaseUrl + "/api/mock/oa/approvals") -H ("X-Trace-Id: " + $using:trace) -H ("X-Request-Id: " + $using:rid3) -H "Content-Type: application/json" -d $using:b3
  $b = Get-Content -Raw $bf
  Remove-Item $bf -Force -ErrorAction SilentlyContinue
  return $b.Trim()
} -ThrottleLimit 5
$bodies = $conc | ForEach-Object { $_ | ConvertFrom-Json }
$falseCount = @($bodies | Where-Object { $_.dedup -eq $false }).Count
$trueCount = @($bodies | Where-Object { $_.dedup -eq $true }).Count
$ids = @($bodies | Select-Object -ExpandProperty approval_id -Unique)
Check "21 5 concurrent same request_id -> 1xfalse + 4xtrue, single approval_id" (($falseCount -eq 1) -and ($trueCount -eq 4) -and ($ids.Count -eq 1)) ("false=" + $falseCount + " true=" + $trueCount + " distinct_approval_id=" + $ids.Count + " id=" + ($ids -join ","))
Write-Output "     concurrent raw bodies:"
foreach ($bb in $bodies) { Write-Output ("       " + ($bb | ConvertTo-Json -Compress)) }
Write-Output ("     concurrent request_id = " + $rid3)

Write-Output "=================================================================="
Write-Output ("RESULT profile=" + $Label + "  PASS=" + $script:pass + "  FAIL=" + $script:fail)
Write-Output "=================================================================="
if ($script:fail -gt 0) { exit 1 } else { exit 0 }
