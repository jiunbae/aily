# Bash completion for aily CLI
_aily_completions() {
    local cur prev cmd commands cmd_index arg_index i word
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]:-}"
    cmd=""
    cmd_index=-1

    commands="init status sessions sync logs tail attach export config doctor start stop auto uninstall help version"

    _aily_fetch_sessions() {
        # Delegate to `aily sessions --json` to respect configured dashboard/auth/fallback logic.
        if command -v aily >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
            aily sessions --json 2>/dev/null | jq -r \
                '(if type == "array" then . else .sessions // [] end) | .[] | .name' 2>/dev/null
            return
        fi
        if command -v tmux >/dev/null 2>&1; then
            tmux list-sessions -F '#{session_name}' 2>/dev/null || true
        fi
    }

    for ((i=1; i<COMP_CWORD; i++)); do
        word="${COMP_WORDS[i]}"
        case "$word" in
            --json|--verbose|--help) continue ;;
            -*) continue ;;
            *)
                cmd="$word"
                cmd_index=$i
                break
                ;;
        esac
    done

    if [[ -z "$cmd" ]]; then
        COMPREPLY=($(compgen -W "$commands --json --help --verbose" -- "$cur"))
        return 0
    fi

    arg_index=$((COMP_CWORD - cmd_index))

    case "$cmd" in
        sessions)
            COMPREPLY=($(compgen -W "--json --ssh" -- "$cur"))
            return 0
            ;;
        logs|tail)
            if [[ "$prev" == "--limit" || "$prev" == "-n" ]]; then
                return 0
            fi
            local sessions
            sessions="$(_aily_fetch_sessions)"
            COMPREPLY=($(compgen -W "--json --limit -n $sessions" -- "$cur"))
            return 0
            ;;
        sync|attach|start|stop)
            local sessions
            sessions="$(_aily_fetch_sessions)"
            COMPREPLY=($(compgen -W "$sessions" -- "$cur"))
            return 0
            ;;
        export)
            if [[ "$arg_index" -eq 1 ]]; then
                local sessions
                sessions="$(_aily_fetch_sessions)"
                COMPREPLY=($(compgen -W "$sessions" -- "$cur"))
            elif [[ "$arg_index" -eq 2 ]]; then
                COMPREPLY=($(compgen -W "json markdown" -- "$cur"))
            fi
            return 0
            ;;
        config)
            if [[ "$arg_index" -eq 1 ]]; then
                COMPREPLY=($(compgen -W "show set dashboard-url" -- "$cur"))
            fi
            return 0
            ;;
        auto)
            if [[ "$arg_index" -eq 1 ]]; then
                COMPREPLY=($(compgen -W "on off" -- "$cur"))
            fi
            return 0
            ;;
    esac
}

complete -F _aily_completions aily
