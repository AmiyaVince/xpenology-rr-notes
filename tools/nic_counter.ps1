<#
.SYNOPSIS
    采样 Windows 网卡收发计数，无屏幕时判断对端有没有发包。

.DESCRIPTION
    在没有显示器、没有串口的情况下，把 PC 网口当成“示波器”：
    直连 NAS 后，如果 NAS 的 DHCP/ARP/mDNS 在发，计数会持续增长；
    如果 20 秒内单播/广播/组播全部 +0，说明对端操作系统没把网卡跑起来。

.PARAMETER InterfaceAlias
    网卡名称，例如 "以太网"。省略时自动选择第一个 Up 的物理以太网卡。

.PARAMETER Seconds
    采样总时长，默认 20 秒。

.PARAMETER IntervalMs
    采样间隔，默认 1000 毫秒。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\nic_counter.ps1
    powershell -ExecutionPolicy Bypass -File .\nic_counter.ps1 -InterfaceAlias "以太网" -Seconds 30
#>

param(
    [string]$InterfaceAlias = "",
    [int]$Seconds = 20,
    [int]$IntervalMs = 1000
)

$ErrorActionPreference = "Stop"

if (-not $InterfaceAlias) {
    $adapter = Get-NetAdapter -Physical |
        Where-Object { $_.Status -eq "Up" -and $_.MediaType -eq "802.3" } |
        Select-Object -First 1
    if (-not $adapter) {
        throw "没有找到 Up 状态的物理以太网卡，请用 -InterfaceAlias 指定。"
    }
    $InterfaceAlias = $adapter.Name
}

$adapter = Get-NetAdapter -Name $InterfaceAlias
if (-not $adapter) {
    throw "找不到网卡：$InterfaceAlias"
}

Write-Host "接口   : $($adapter.Name) [$($adapter.InterfaceDescription)]"
Write-Host "MAC    : $($adapter.MacAddress)"
Write-Host "链路   : $($adapter.LinkSpeed) / $($adapter.Status)"
Write-Host "采样   : ${Seconds}s，每 ${IntervalMs}ms 一次"
Write-Host ""

$start = Get-NetAdapterStatistics -Name $InterfaceAlias
$last = $start

Write-Host ("{0,-8} {1,12} {2,12} {3,12} {4,12}" -f "t(s)", "RX单播+", "RX广播+", "RX组播+", "TX单播+")
Write-Host ("-" * 62)

$elapsed = 0
while ($elapsed -lt $Seconds) {
    Start-Sleep -Milliseconds $IntervalMs
    $elapsed += $IntervalMs / 1000.0

    $now = Get-NetAdapterStatistics -Name $InterfaceAlias
    $rxU = $now.ReceivedUnicastPackets - $last.ReceivedUnicastPackets
    $rxB = $now.ReceivedBroadcastPackets - $last.ReceivedBroadcastPackets
    $rxM = $now.ReceivedMulticastPackets - $last.ReceivedMulticastPackets
    $txU = $now.SentUnicastPackets - $last.SentUnicastPackets

    Write-Host ("{0,-8:N1} {1,12} {2,12} {3,12} {4,12}" -f $elapsed, $rxU, $rxB, $rxM, $txU)
    $last = $now
}

$end = Get-NetAdapterStatistics -Name $InterfaceAlias
$totalRxU = $end.ReceivedUnicastPackets - $start.ReceivedUnicastPackets
$totalRxB = $end.ReceivedBroadcastPackets - $start.ReceivedBroadcastPackets
$totalRxM = $end.ReceivedMulticastPackets - $start.ReceivedMulticastPackets
$totalTxU = $end.SentUnicastPackets - $start.SentUnicastPackets

Write-Host ("-" * 62)
Write-Host "总计：RX单播 $totalRxU / RX广播 $totalRxB / RX组播 $totalRxM / TX单播 $totalTxU"

if (($totalRxU + $totalRxB + $totalRxM) -eq 0) {
    Write-Host ""
    Write-Host "结论：这段时间内没有收到任何包。" -ForegroundColor Yellow
    Write-Host "      链路 Up 只证明 PHY 通；对端操作系统可能没有把网卡跑起来。"
} else {
    Write-Host ""
    Write-Host "结论：收到流量，对端在发包。继续看 DHCP/ARP/mDNS 的具体内容。" -ForegroundColor Green
}
