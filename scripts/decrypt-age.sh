#!/bin/sh
set -eu
# Private identities rendered from kv/codestra/postgresql/<env>/backup#identity.
: "${OPENBAO_BACKUP_IDENTITY_FILE:?OpenBao-rendered identity file required}"
exec age --decrypt --identity "$OPENBAO_BACKUP_IDENTITY_FILE" --output "$2" "$1"
