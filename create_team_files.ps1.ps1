# /NFL_GM_Sim/create_team_files.ps1
# Creates empty seed files for all remaining teams

$teams = @(
    "packers",  "eagles",   "ravens",   "bills",
    "49ers",    "bengals",  "cowboys",  "browns",
    "steelers", "commanders","giants",  "jets",
    "patriots", "texans",   "colts",    "jaguars",
    "titans",   "broncos",  "raiders",  "chargers",
    "falcons",  "panthers", "saints",   "buccaneers",
    "cardinals","rams",     "seahawks", "dolphins"
)

$basePath = "Z:\NFL_GM_SIM\database"

foreach ($team in $teams) {
    $files = @(
        "seed_$team.py",
        "seed_depth_chart_$team.py",
        "seed_flex_$team.py"
    )

    foreach ($file in $files) {
        $fullPath = Join-Path $basePath $file
        if (-not (Test-Path $fullPath)) {
            New-Item -ItemType File -Path $fullPath | Out-Null
            Write-Host "  Created: $file"
        } else {
            Write-Host "  Exists:  $file (skipped)"
        }
    }
}

Write-Host "`n✅ Done. Files created in $basePath"