#!/usr/bin/env bash
# Not run by hand (it is sourced): who owns the files BlueStick's scripts and
# containers rely on.  ONE check, read by two callers —
#
#   deploy.sh        lists the findings before it writes anything (options
#                    1-3, and under option 5) and offers the fixes;
#   collect-logs.sh  writes the same findings into the diagnostics bundle
#                    (permissions.txt), by KIND only: the sentences below
#                    name accounts and paths, and a bundle must not.
#
# It reads the current directory (both callers `cd` to the project's folder)
# and changes nothing.  A new file a script or a container depends on gets
# its check HERE, with a kind — never a second audit in a caller.
#
# After `audit_permissions`, four parallel arrays hold the findings:
#   PERM_KIND   a fixed word for the finding (the list is in the function)
#   PERM_WHAT   what is wrong and why it matters, for a person at a terminal
#   PERM_FIX    the one command that fixes it
#   PERM_AUTO   what the caller may do itself, or "" — only ever
#               `chmod600:<path>` or `chown-uploads`, never text to evaluate
#
# `perm_describe_uploads` says what uploads/ is in one sentence, correct or
# not (deploy.sh option 5) — never into a bundle: it names the host's account.

PERM_KIND=(); PERM_WHAT=(); PERM_FIX=(); PERM_AUTO=()
# The user the application's containers run as.
CONTAINER_UID=999

perm_owner() { stat -c '%u' "$1" 2>/dev/null || echo -1; }
perm_owner_name() { stat -c '%U' "$1" 2>/dev/null || echo '?'; }
# What `ls -l` shows for the containers' user on THIS host.  UID 999 is
# whatever account the host gave that number (often dnsmasq, and GID 999
# systemd-journal): the containers do not use that account, they share the
# number.  Said wherever the number is named, so a correct owner does not
# read as a mistake (diagnostics bundle 7, 2026-10-15).
perm_container_uid_note() {
    local name
    name="$(getent passwd "$CONTAINER_UID" 2>/dev/null | cut -d: -f1)"
    if [[ -n "$name" ]]; then
        echo "\`ls -l\` shows UID $CONTAINER_UID as \"$name\" on this host: that is only this host's name for the number the containers run as, and it is expected"
    else
        echo "\`ls -l\` shows UID $CONTAINER_UID as a number on this host: the containers run as that number, and it is expected"
    fi
}
perm_mode() { stat -c '%a' "$1" 2>/dev/null || echo '?'; }
# True when the group or anyone else has any access to it.
perm_open_to_others() {
    local m; m="$(stat -c '%a' "$1" 2>/dev/null)" || return 1
    [[ -n "$m" && "${m: -2}" != "00" ]]
}
# Whose it is, as a word that names nobody: the account running this, root,
# the containers' user, or someone else.
perm_owner_class() {
    local owner; owner="$(perm_owner "$1")"
    if [[ "$owner" == "-1" ]]; then echo "unreadable"
    elif [[ "$owner" == "$(id -u)" ]]; then echo "this account"
    elif [[ "$owner" == "0" ]]; then echo "root"
    elif [[ "$owner" == "$CONTAINER_UID" ]]; then echo "the containers' user"
    else echo "another account"; fi
}
# <kind> <what is wrong> <the command that fixes it> [<auto fix>]
perm_note() { PERM_KIND+=("$1"); PERM_WHAT+=("$2"); PERM_FIX+=("$3"); PERM_AUTO+=("${4:-}"); }

audit_permissions() {
    PERM_KIND=(); PERM_WHAT=(); PERM_FIX=(); PERM_AUTO=()
    local uid me owner f auto root
    uid="$(id -u)"; me="$(id -un)"
    root="${PROJECT_ROOT:-$PWD}"

    if [[ "$uid" -eq 0 ]]; then
        perm_note run_as_root \
            "This script is running as root. Nothing in it needs root, and every file it writes (.env, ssl/, backups) becomes root's: the next run as an ordinary account cannot renew the certificate, back up the key or rewrite .env." \
            "Stop, and run it as an ordinary account that can use Docker (a member of the docker group) — the same one every time. To hand this folder back first:  chown -R ${SUDO_USER:-<that account>}: \"$root\" && chown -R $CONTAINER_UID:$CONTAINER_UID \"$root/uploads\""
    fi

    if [[ -e .env ]]; then
        owner="$(perm_owner .env)"
        if [[ "$uid" -ne 0 && "$owner" != "$uid" ]]; then
            perm_note env_other_owner \
                ".env belongs to $(perm_owner_name .env), not to $me: this script cannot update it (options 2 and 3 rewrite it)." \
                "sudo chown $me: \"$root/.env\""
        fi
        if perm_open_to_others .env; then
            auto=""; [[ "$uid" -eq 0 || "$owner" == "$uid" ]] && auto="chmod600:.env"
            perm_note env_readable_by_others \
                ".env (mode $(perm_mode .env)) can be read by other accounts on this host. It holds SECRET_KEY and the database password." \
                "chmod 600 \"$root/.env\"" "$auto"
        fi
    fi

    local foreign=()
    for f in ssl ssl/certs ssl/certs/networkmapper.crt ssl/certs/networkmapper.key; do
        [[ -e "$f" ]] || continue
        owner="$(perm_owner "$f")"
        if [[ "$uid" -ne 0 && "$owner" != "$uid" ]]; then foreign+=("$f ($(perm_owner_name "$f"))"); fi
    done
    if [[ ${#foreign[@]} -gt 0 ]]; then
        perm_note ssl_other_owner \
            "The certificate folder is not $me's — ${foreign[*]}. nginx reads it whoever owns it, so root is not needed there; but this script then cannot renew the certificate (option 3) or back up the key (option 6), and the local CA's install step refuses." \
            "sudo chown -R $me: \"$root/ssl\""
    fi
    f="ssl/certs/networkmapper.key"
    if [[ -e "$f" ]] && perm_open_to_others "$f"; then
        owner="$(perm_owner "$f")"
        auto=""; [[ "$uid" -eq 0 || "$owner" == "$uid" ]] && auto="chmod600:$f"
        perm_note tls_key_readable_by_others \
            "The TLS private key $f (mode $(perm_mode "$f")) can be read by other accounts on this host." \
            "chmod 600 \"$root/$f\"" "$auto"
    fi

    if [[ -d uploads ]]; then
        owner="$(perm_owner uploads)"
        if [[ "$owner" != "$CONTAINER_UID" && "$owner" != "$uid" ]]; then
            auto=""; [[ "$uid" -eq 0 ]] && auto="chown-uploads"
            perm_note uploads_other_owner \
                "uploads/ belongs to $(perm_owner_name uploads); it should be the containers' (UID $CONTAINER_UID). The backend takes it over on its next start (as root inside the container), so before a first start this needs nothing. If it is still so AFTER a start, Docker could not change its owner there (rootless Docker, an NFS share with root_squash, SELinux) — the backend log's [entrypoint] lines say why. After the fix, $(perm_container_uid_note)." \
                "sudo chown -R $CONTAINER_UID:$CONTAINER_UID \"$root/uploads\"" "$auto"
        fi
    fi
}

# One sentence on uploads/ for a person at a terminal, whatever its state:
# the CORRECT state — another number's folder, mode 700, that this account
# cannot list — reads like a fault, and operators took it for one.
perm_describe_uploads() {
    if [[ ! -e uploads ]]; then
        echo "uploads/ does not exist yet: the deploy makes it, and the containers take it over at their first start."
        return 0
    fi
    local owner mode
    owner="$(perm_owner uploads)"; mode="$(perm_mode uploads)"
    if [[ "$owner" == "$CONTAINER_UID" ]]; then
        echo "uploads/ is the containers' (UID $CONTAINER_UID, mode $mode), as it should be: this account cannot list it, by design, and backups read it through a container. $(perm_container_uid_note). To look inside:  ${DC:-docker compose} exec backend ls -la /app/uploads"
    elif [[ "$owner" == "$(id -u)" ]]; then
        echo "uploads/ is still this account's (mode $mode): the containers take it over at their next start."
    else
        echo "uploads/ belongs to $(perm_owner_name uploads) (mode $mode), not to the containers — see the finding below."
    fi
}
