param(
    [ValidateSet("codex", "claude", "opencode", "all")]
    [string]$Agent = "codex",
    [ValidateSet("user", "project")]
    [string]$Scope = "user",
    [string]$ProjectDir = "",
    [string[]]$Pack = @(),
    [switch]$WithRuntime,
    [switch]$Pull
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @("$repoRoot\tools\zju_skills.py", "update", "--agent", $Agent, "--scope", $Scope)
if ($ProjectDir) { $arguments += @("--project-dir", $ProjectDir) }
foreach ($item in $Pack) { $arguments += @("--pack", $item) }
if ($WithRuntime) { $arguments += "--with-runtime" }
if ($Pull) { $arguments += "--pull" }
& python @arguments
exit $LASTEXITCODE
