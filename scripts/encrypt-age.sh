#!/bin/sh
set -eu
# Public recipient list rendered from kv/codestra/postgresql/<env>/backup#recipients.
: "${OPENBAO_BACKUP_RECIPIENTS_FILE:?OpenBao-rendered recipients file required}"
exec age --encrypt --recipients-file "$OPENBAO_BACKUP_RECIPIENTS_FILE" --output "$2" "$1"
