$ErrorActionPreference = 'Stop'
$database = 'logiq_restore_' + [Guid]::NewGuid().ToString('N')
$snapshot = "/tmp/$database.dump"
$folder = Join-Path $PSScriptRoot '..\.backups'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$destination = Join-Path $folder "$database.dump"
& docker exec logiq-postgres-1 pg_dump -U logiq -d logiq -Fc -f $snapshot
if ($LASTEXITCODE -ne 0) { throw 'Snapshot failed; no restore database was created.' }
& docker cp "logiq-postgres-1:$snapshot" $destination
if ($LASTEXITCODE -ne 0) { throw 'Snapshot copy failed.' }
& docker exec logiq-postgres-1 createdb -U logiq $database
if ($LASTEXITCODE -ne 0) { throw 'Restore database creation failed.' }
try {
    & docker exec logiq-postgres-1 pg_restore -U logiq -d $database --exit-on-error $snapshot
    if ($LASTEXITCODE -ne 0) { throw 'Restore failed.' }
    & docker exec logiq-postgres-1 psql -U logiq -d $database -c 'SELECT version_num FROM alembic_version; SELECT count(*) AS customers FROM organizations; SELECT count(*) AS log_events FROM logs;'
    if ($LASTEXITCODE -ne 0) { throw 'Restored data verification failed.' }
    Write-Host "PASS: snapshot restored into a separate temporary database. Backup: $destination"
} finally {
    # Only the uniquely generated rehearsal database is removed.
    & docker exec logiq-postgres-1 dropdb -U logiq $database
}
